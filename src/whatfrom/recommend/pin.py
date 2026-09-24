"""검증을 통과한 Dockerfile의 FROM을 수집 시점 digest로 고정한다.

digest는 LLM이 아니라 코드가 붙인다. LLM은 digest를 보지도 쓰지도 않으므로 불변식이
그대로다. 고정한 digest는 수집 시점의 이미지라, 이동 태그는 그 뒤 바뀌었을 수 있다.
"""

from collections.abc import Mapping

from whatfrom.recommend.verify import image_ref_spans


def pin_dockerfile(dockerfile: str, digests: Mapping[str, str | None]) -> tuple[str, list[str]]:
    """FROM이 가리키는 이미지를 image@digest로 바꾼다. (고친 Dockerfile, digest가 없던 이미지).

    검증과 같은 파서로 FROM을 찾는다. 태그 이름은 남기고 뒤에 digest만 붙인다.
    digests에 없는 참조와 이미 @가 붙은 참조는 건드리지 않는다.
    """
    parts: list[str] = []
    missing: list[str] = []
    last = 0
    for ref, (_, end) in image_ref_spans(dockerfile):
        if "@" in ref or ref not in digests:
            continue
        digest = digests[ref]
        if digest is None:
            if ref not in missing:
                missing.append(ref)
            continue
        parts += [dockerfile[last:end], f"@{digest}"]
        last = end
    parts.append(dockerfile[last:])
    return "".join(parts), missing
