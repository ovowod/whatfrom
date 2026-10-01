"""정답 dataset(golden set)을 읽고 검색·추천 결과를 채점하는 평가 도구.

다른 stage package를 직접 참조하지 않으며 공통 계약은 whatfrom.core에서 가져온다.
검색·추천 실행과 평가를 연결하는 역할은 cli.py가 맡는다.
이 의존성 규칙은 tests/test_layering.py에서 검사한다.

golden set을 읽으려면 dev 의존성 group의 PyYAML이 필요하다.
cli.py는 PyYAML을 쓰는 module을 명령 함수 안에서 가져오므로, CLI를 import하거나
다른 명령을 실행할 때는 PyYAML이 없어도 된다. golden set을 읽는 eval과
load-questions 명령을 실행할 때만 필요하다. API 서버는 이 평가 module을 가져오지 않는다.
"""
