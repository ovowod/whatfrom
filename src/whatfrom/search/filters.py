# src/whatfrom/search/filters.py
"""검색 조건을 image_tags에 거는 SQL 조건으로 옮긴다.

조건은 SQLAlchemy 표현식으로만 만든다. 문자열 SQL을 조립하지 않으므로 LLM이 뽑은
값이 SQL이 될 경로가 없다. 판정 의미는 eval의 조건 채점과 같게 맞추지만 코드는
공유하지 않는다. 채점이 시스템 코드를 재사용하면 시스템이 틀릴 때 함께 틀린다.
"""

from collections.abc import Iterator
from dataclasses import dataclass, replace

from sqlalchemy import ColumnElement, and_, func, not_, or_, select

from whatfrom.core.models import ImageTag, ImageVariant
from whatfrom.core.versions import VERSION_BOUNDARIES

MB = 1_000_000


@dataclass(frozen=True)
class TagConditions:
    version_prefix: str | None = None
    architectures: tuple[str, ...] = ()
    distributions: tuple[str, ...] = ()
    exclude_distributions: tuple[str, ...] = ()
    max_size_mb: float | None = None

    @property
    def empty(self) -> bool:
        return self == TagConditions()


def tag_filters(conditions: TagConditions) -> list[ColumnElement[bool]]:
    """ImageTag 행에 거는 조건 목록. 모두 만족해야 한다."""
    filters: list[ColumnElement[bool]] = []
    # NULL과 비교하면 결과가 NULL이고, NOT을 씌워도 NULL이라 행이 사라진다.
    # 빈 문자열로 바꿔 비교해 "값이 다르다"로 판정되게 한다.
    distribution = func.coalesce(ImageTag.distribution, "")
    codename = func.coalesce(ImageTag.distro_codename, "")
    underived = and_(ImageTag.distribution.is_(None), ImageTag.distro_codename.is_(None))

    if conditions.distributions:
        values = list(conditions.distributions)
        filters.append(
            or_(
                distribution.in_(values),
                codename.in_(values),
                # 파생 값이 없으면 태그 이름으로 판정한다.
                and_(underived, or_(*(ImageTag.tag.contains(v, autoescape=True) for v in values))),
            )
        )
    for value in conditions.exclude_distributions:
        filters.append(
            not_(
                or_(
                    distribution == value,
                    codename == value,
                    and_(underived, ImageTag.tag.contains(value, autoescape=True)),
                )
            )
        )
    if conditions.version_prefix is not None:
        prefix = conditions.version_prefix
        version = func.coalesce(ImageTag.language_version, ImageTag.tag)
        # autoescape가 LIKE의 _와 %를 이스케이프한다. _는 temurin 버전 21.0.12_8에
        # 실제로 나오는 문자라 그대로 두면 한 글자 와일드카드가 된다.
        filters.append(
            or_(
                version == prefix,
                *(version.startswith(prefix + b, autoescape=True) for b in VERSION_BOUNDARIES),
            )
        )
    for architecture in conditions.architectures:
        filters.append(_linux_variant(ImageVariant.architecture == architecture))
    if conditions.max_size_mb is not None:
        # 첫 요구 아키텍처, 없으면 amd64에서 가장 작은 이미지가 상한 이하여야 한다.
        architecture = conditions.architectures[0] if conditions.architectures else "amd64"
        filters.append(
            _linux_variant(
                ImageVariant.architecture == architecture,
                ImageVariant.size_bytes <= conditions.max_size_mb * MB,
            )
        )
    return filters


def _linux_variant(*criteria: ColumnElement[bool]) -> ColumnElement[bool]:
    return (
        select(ImageVariant.id)
        .where(ImageVariant.tag_id == ImageTag.id, ImageVariant.os == "linux", *criteria)
        .exists()
    )


def relaxations(conditions: TagConditions) -> Iterator[tuple[TagConditions, str | None]]:
    """조건을 약한 것부터 하나씩 푼 단계들. 첫 단계는 푸지 않은 원래 조건이다.

    크기 → 배포판(지정과 제외 함께) → 아키텍처 → 버전 순서다. 크기는 선호에 가깝고
    버전 고정은 가장 강한 요구로 본다. 비어 있는 조건은 풀 것이 없어 건너뛴다.
    두 번째 값은 그 단계에서 푼 조건의 알림이다.
    """
    yield conditions, None
    current = conditions
    if current.max_size_mb is not None:
        current = replace(current, max_size_mb=None)
        yield current, f"크기 조건({conditions.max_size_mb:g}MB 이하)"
    if current.distributions or current.exclude_distributions:
        described = ", ".join(
            [*current.distributions, *(f"{v} 제외" for v in current.exclude_distributions)]
        )
        current = replace(current, distributions=(), exclude_distributions=())
        yield current, f"배포판 조건({described})"
    if current.architectures:
        described = ", ".join(current.architectures)
        current = replace(current, architectures=())
        yield current, f"아키텍처 조건({described})"
    if current.version_prefix is not None:
        described = current.version_prefix
        current = replace(current, version_prefix=None)
        yield current, f"버전 조건({described})"
