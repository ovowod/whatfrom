from datetime import UTC, datetime, timedelta

from whatfrom.search.tagselect import TagRef, select_tags


def ref(
    tag_id: int,
    tag: str,
    digest: str | None = "",
    pushed: datetime | None = None,
) -> TagRef:
    """테스트용 TagRef. digest를 안 주면 tag_id로 유일한 값을 만든다."""
    return TagRef(
        id=tag_id,
        tag=tag,
        manifest_digest=f"sha256:{tag_id}" if digest == "" else digest,
        last_pushed_at=pushed,
    )


def test_prerelease_tags_never_become_candidates():
    """rc만 거르면 알파와 베타를 놓친다. 파이썬은 PEP 440 표기를 쓴다."""
    tags = [
        ref(1, "3.15.0a8"),
        ref(2, "3.15.0b1"),
        ref(3, "3.15.0rc2"),
        ref(4, "3.15-rc"),
        ref(5, "3.15-rc-slim"),
        ref(6, "3.14-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-slim"]


def test_milestone_tags_are_excluded():
    """redis의 8.8-m03 같은 마일스톤 빌드는 후보가 되지 않는다."""
    tags = [ref(1, "8.8-m03"), ref(2, "8.8-slim")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["8.8-slim"]


def test_devel_tag_is_excluded():
    """ubuntu의 devel은 개발 브랜치라 후보가 되지 않는다."""
    tags = [ref(1, "devel"), ref(2, "26.4-slim")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["26.4-slim"]


def test_a_tag_sharing_a_digest_with_a_prerelease_is_excluded_too():
    """ubuntu의 devel과 26.10은 같은 digest를 가리킨다.

    26.10은 이름만 보면 안정 버전 같지만 실제로는 아직 나오지 않은
    개발 브랜치라, digest로 전파해서 함께 뺀다.
    """
    tags = [
        ref(1, "devel", digest="sha256:same"),
        ref(2, "26.10", digest="sha256:same"),
        ref(3, "26.4-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["26.4-slim"]


def test_digest_propagation_ignores_unknown_digests():
    """digest가 None인 태그는 전파의 근거로도, 대상으로도 쓰지 않는다."""
    tags = [
        ref(1, "devel", digest=None),
        ref(2, "26.4-slim", digest=None),
    ]
    assert {t.tag for t in select_tags(tags, limit=10)} == {"26.4-slim"}


def test_python_like_repo_uses_minor_lines_when_there_is_one_major():
    """메이저 3은 하나뿐이라 줄기가 되지 못하고, 3.14와 3.13이 줄기가 된다.

    latest와 3은 줄기 별칭이 아니고, 3.14.7-slim은 패치에 묶여 있어 후보가 아니다.
    """
    tags = [
        ref(1, "latest"),
        ref(2, "3"),
        ref(3, "3.14.7-slim"),
        ref(4, "3.14-slim"),
        ref(5, "3.13-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-slim", "3.13-slim"]


def test_same_digest_keeps_only_the_shorter_name():
    """3.14-alpine3.24는 3.14-alpine과 같은 이미지다."""
    tags = [
        ref(1, "3.14-alpine3.24", digest="sha256:same"),
        ref(2, "3.14-alpine", digest="sha256:same"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-alpine"]


def test_same_digest_and_same_length_breaks_the_tie_alphabetically():
    tags = [
        ref(1, "3.14-zzzz", digest="sha256:same"),
        ref(2, "3.14-aaaa", digest="sha256:same"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-aaaa"]


def test_tags_without_a_digest_are_not_folded():
    """동일성을 알 수 없는 것을 같다고 단정하지 않는다."""
    tags = [
        ref(1, "3.14-slim", digest=None),
        ref(2, "3.14-alpine", digest=None),
    ]
    assert {t.tag for t in select_tags(tags, limit=10)} == {"3.14-slim", "3.14-alpine"}


def test_newer_versions_come_first():
    tags = [ref(1, "3.9-slim"), ref(2, "3.14-slim"), ref(3, "4.0-slim"), ref(4, "3.10-slim")]
    assert [t.tag for t in select_tags(tags, limit=10)] == [
        "4.0-slim",
        "3.14-slim",
        "3.10-slim",
        "3.9-slim",
    ]


def test_shorter_names_come_first_within_the_same_version():
    """짧은 이름이 각 변종의 대표다. 사전순만 쓰면 alpine 파생형이 자리를 먹는다."""
    tags = [
        ref(1, "3.14-alpine3.22"),
        ref(2, "3.14-bookworm"),
        ref(3, "3.14"),
        ref(4, "3.14-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == [
        "3.14",
        "3.14-slim",
        "3.14-bookworm",
        "3.14-alpine3.22",
    ]


def test_falls_back_to_recent_push_when_no_tag_has_a_version():
    """debian:bookworm처럼 숫자 버전이 없는 리포. 후보를 0개로 만들지 않는다."""
    tags = [
        ref(1, "bookworm", pushed=datetime(2026, 9, 1, tzinfo=UTC)),
        ref(2, "trixie", pushed=datetime(2026, 9, 3, tzinfo=UTC)),
        ref(3, "bullseye", pushed=datetime(2026, 8, 1, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["trixie", "bookworm", "bullseye"]


def test_fallback_still_excludes_prereleases():
    """이동 별칭이 없어도 프리릴리스는 후보가 되지 않는다."""
    tags = [
        ref(1, "trixie-rc", pushed=datetime(2026, 9, 5, tzinfo=UTC)),
        ref(2, "bookworm", pushed=datetime(2026, 9, 1, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["bookworm"]


def test_fallback_puts_tags_without_a_push_time_last():
    tags = [
        ref(1, "bookworm", pushed=None),
        ref(2, "trixie", pushed=datetime(2026, 9, 3, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["trixie", "bookworm"]


def test_returns_prereleases_when_a_repo_has_nothing_else():
    """후보가 0개인 것보다는 프리릴리스라도 돌려주는 편이 낫다."""
    tags = [
        ref(1, "3.15.0a8", pushed=datetime(2026, 9, 1, tzinfo=UTC)),
        ref(2, "3.15.0b1", pushed=datetime(2026, 9, 3, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.15.0b1", "3.15.0a8"]


def test_returns_what_exists_when_fewer_than_the_limit():
    assert len(select_tags([ref(1, "3.14-slim")], limit=5)) == 1


def test_returns_empty_for_no_tags():
    assert select_tags([], limit=5) == []


DAY = timedelta(days=1)
NOW = datetime(2026, 9, 17, tzinfo=UTC)


def test_node_like_repo_uses_major_lines():
    """26, 24, 22가 모두 별칭이면 26.8 같은 마이너가 있어도 메이저 단위다."""
    tags = [
        ref(1, "26"),
        ref(2, "26.8"),
        ref(3, "24"),
        ref(4, "22"),
        ref(5, "24.11-alpine"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["26", "24", "22"]


def test_ubuntu_like_repo_needs_an_existing_alias_tag():
    """26이나 24라는 태그가 없으면 그 자릿수는 줄기가 아니다."""
    tags = [ref(1, "26.04"), ref(2, "24.04"), ref(3, "22.04")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["26.04", "24.04", "22.04"]


def test_a_single_supported_line_is_kept_without_falling_back():
    """17은 지원이 끝났다. 줄기가 하나 남았다고 폴백하면 17이 되살아난다."""
    tags = [
        ref(1, "18", pushed=NOW),
        ref(2, "18-alpine", pushed=NOW),
        ref(3, "17", pushed=NOW - 100 * DAY),
        ref(4, "17-alpine", pushed=NOW - 100 * DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18", "18-alpine"]


def test_a_repo_with_only_one_line_folds_digests():
    """줄기가 원래 하나여도 줄기 경로를 타고 같은 이미지를 묶는다."""
    tags = [
        ref(1, "3.14-alpine3.24", digest="sha256:same"),
        ref(2, "3.14-alpine", digest="sha256:same"),
        ref(3, "3.14-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-slim", "3.14-alpine"]


def test_a_dotted_suffix_is_not_an_alias_of_the_shorter_line():
    """24.1은 24를 따라 움직이지 않는다. 줄기 24의 후보는 24뿐이다."""
    tags = [ref(1, "24"), ref(2, "24.1"), ref(3, "22")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["24", "22"]


def test_a_numeric_hyphen_suffix_is_not_an_alias():
    """24-1은 24의 변형이 아니다."""
    tags = [ref(1, "24"), ref(2, "24-1"), ref(3, "22")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["24", "22"]


def test_a_line_sixty_days_behind_is_supported_but_sixty_one_is_not():
    tags = [
        ref(1, "18", pushed=NOW),
        ref(2, "17", pushed=NOW - 60 * DAY),
        ref(3, "16", pushed=NOW - 61 * DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18", "17"]


def test_lines_take_turns_up_to_the_limit():
    """최신 줄기부터 몰아 채우면 상한 안에 26의 변형만 들어가 LTS 줄기가 빠진다."""
    tags = [
        ref(1, "26"),
        ref(2, "26-slim"),
        ref(3, "26-alpine"),
        ref(4, "24"),
        ref(5, "24-slim"),
        ref(6, "22"),
    ]
    assert [t.tag for t in select_tags(tags, limit=3)] == ["26", "24", "22"]


def test_the_limit_can_stop_a_round_midway():
    """줄기가 상한보다 많으면 한 바퀴를 다 돌기 전에 멈춘다."""
    tags = [ref(1, "26"), ref(2, "24"), ref(3, "22")]
    assert [t.tag for t in select_tags(tags, limit=2)] == ["26", "24"]


def test_patch_pins_do_not_outvote_a_single_minor_line():
    """패치 고정 태그 둘은 두 줄기가 아니라 한 줄기의 스냅샷이다.

    둘을 줄기로 세면 자릿수 3이 뽑혀 별칭이 빠지고 3.14.7, 3.14.6만 남는다.
    """
    tags = [
        ref(1, "3.14-alpine"),
        ref(2, "3.14-slim"),
        ref(3, "3.14.7"),
        ref(4, "3.14.6"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-slim", "3.14-alpine"]


def test_patch_lines_are_used_when_no_shorter_alias_exists():
    """별칭이 패치 자릿수에만 있으면 그 자릿수를 줄기로 쓴다. 폴백이면 latest가 섞인다."""
    tags = [
        ref(1, "latest", pushed=NOW),
        ref(2, "1.2.3", pushed=NOW - DAY),
        ref(3, "1.2.4", pushed=NOW - 2 * DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["1.2.4", "1.2.3"]


def test_a_date_snapshot_is_not_a_release_line():
    """alpine의 20260805는 edge 스냅샷이다. 버전처럼 보여도 줄기로 세면 단위가 메이저로 틀어진다."""
    tags = [
        ref(1, "3"),
        ref(2, "3.24"),
        ref(3, "3.23"),
        ref(4, "20260805"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.24", "3.23"]


def test_new_prerelease_words_are_excluded():
    """숫자 버전이 없는 리포지토리는 폴백으로 가므로, 이름만으로 걸러져야 한다."""
    tags = [
        ref(1, "19beta3", pushed=NOW),
        ref(2, "tip-bookworm", pushed=NOW),
        ref(3, "trixie-backports", pushed=NOW),
        ref(4, "sid", pushed=NOW),
        ref(5, "testing", pushed=NOW),
        ref(6, "edge", pushed=NOW),
        ref(7, "trixie", pushed=NOW - DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["trixie"]


def test_a_word_is_only_a_prerelease_at_the_start_of_the_name():
    """이름 중간의 testing은 프리릴리스 표시가 아니다.

    안정 태그를 함께 둔다. 혼자 두면 프리릴리스로 잘못 걸러져도 폴백이 되살려 통과한다.
    """
    tags = [
        ref(1, "bookworm-testing-tools", pushed=NOW),
        ref(2, "trixie", pushed=NOW - DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["bookworm-testing-tools", "trixie"]


def test_windows_only_tags_are_excluded():
    tags = [
        ref(1, "3.14-windowsservercore"),
        ref(2, "3.14-nanoserver"),
        ref(3, "3.14-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-slim"]


def test_a_repo_with_only_windows_tags_has_no_candidates():
    """Linux 컨테이너만 추천한다. 프리릴리스와 달리 폴백으로 되살리지 않는다."""
    tags = [ref(1, "ltsc2022-windowsservercore"), ref(2, "3.14-nanoserver")]
    assert select_tags(tags, limit=10) == []


def test_a_line_without_push_times_is_dropped_when_others_have_them():
    tags = [ref(1, "18", pushed=NOW), ref(2, "17", pushed=None)]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18"]


def test_every_line_is_kept_when_no_push_time_is_known():
    tags = [ref(1, "18"), ref(2, "17")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18", "17"]


def test_the_fallback_folds_digests():
    tags = [
        ref(1, "bookworm-slim", digest="sha256:same", pushed=NOW),
        ref(2, "stable-slim", digest="sha256:same", pushed=NOW),
        ref(3, "trixie", pushed=NOW - DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["stable-slim", "trixie"]


def test_a_lines_reference_time_is_its_latest_push_not_earliest():
    """줄기의 기준 시각은 그 줄기에서 가장 늦은 푸시여야 한다.

    18의 자체 푸시(지금)가 아니라 18-slim의 더 이른 푸시(10일 전)를 기준으로 잘못 잡으면
    (최댓값 대신 최솟값을 쓰면), 줄기 18의 기준 시각이 10일 전으로 밀려 17과의 격차가
    65일에서 55일로 줄어들어 17도 지원 중으로 잘못 판정된다.
    """
    tags = [
        ref(1, "18", pushed=NOW),
        ref(2, "18-slim", pushed=NOW - 10 * DAY),
        ref(3, "17", pushed=NOW - 65 * DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18", "18-slim"]


def test_a_non_alias_tag_still_counts_toward_the_lines_push_time():
    """18.1은 줄기 18의 별칭이 아니지만, 그 푸시 시각은 줄기의 지원 판정에 들어간다.

    18 자신의 푸시(50일 전)는 줄기 18의 최신 시각(18.1의 지금) 기준 60일 안이라
    변형 판정에서도 살아남는다.
    """
    tags = [
        ref(1, "18", pushed=NOW - 50 * DAY),
        ref(2, "18.1", pushed=NOW),
        ref(3, "17", pushed=NOW - 65 * DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18"]


def test_the_prerelease_revival_fallback_folds_digests():
    """프리릴리스만 남아 되살리는 폴백에서도 같은 digest를 가리키는 태그는 묶인다."""
    tags = [
        ref(1, "3.15.0a8", digest="sha256:same", pushed=NOW),
        ref(2, "3.15.0a8-slim", digest="sha256:same", pushed=NOW),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.15.0a8"]


def test_a_stale_variant_alias_is_dropped_but_the_sixty_day_one_is_kept():
    """줄기 자체는 계속 푸시돼도 그 안의 변형은 빌드가 끊길 수 있다.

    redis 실측에서 6-buster가 1,785일 전 푸시인 채로 후보에 남았다.
    """
    tags = [
        ref(1, "18", pushed=NOW),
        ref(2, "18-slim", pushed=NOW - 60 * DAY),
        ref(3, "18-buster", pushed=NOW - 61 * DAY),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18", "18-slim"]


def test_an_undated_alias_is_dropped_when_its_line_has_a_dated_tag():
    tags = [
        ref(1, "18", pushed=NOW),
        ref(2, "18-alpine", pushed=None),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["18"]


def test_undated_aliases_are_kept_when_the_whole_line_has_no_push_time():
    """줄기 전체에 시각이 없으면 변형 판정도 건너뛴다.

    건너뛰지 않고 시각 없는 태그를 걸러내면 두 줄기의 별칭이 모두 사라져 폴백으로
    떨어진다. 폴백은 사전순(24, 24-alpine, 26, 26-alpine)이라 줄기 경로의 번갈아
    채우기 순서(26, 24, 26-alpine, 24-alpine)와 달라 구분된다.
    """
    tags = [
        ref(1, "26"),
        ref(2, "26-alpine"),
        ref(3, "24"),
        ref(4, "24-alpine"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == [
        "26",
        "24",
        "26-alpine",
        "24-alpine",
    ]


def test_the_variant_check_uses_its_own_lines_latest_push_not_the_repos():
    """변형 판정은 그 줄기 자신의 최신 푸시를 기준으로 한다. 리포 전체의 최신 줄기가 아니다.

    19가 리포에서 가장 최근에 푸시됐어도, 18-alpine은 자기 줄기 18의 최신 푸시(55일 전)
    기준으로 55일 뒤처졌을 뿐이라 후보로 남는다. 리포 전체 기준(19의 지금)으로 재면
    110일 뒤처져 잘못 빠진다.
    """
    tags = [
        ref(1, "18", pushed=NOW - 55 * DAY),
        ref(2, "18-alpine", pushed=NOW - 110 * DAY),
        ref(3, "19", pushed=NOW),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["19", "18", "18-alpine"]
