# UCcloud

학교 사설 클라우드의 포털 MVP입니다. OpenStack 2026.1 클러스터의 컨트롤러 `comsv-03`에서 실행 중입니다.

새 팀 이름으로 가입하면 작업 공간이 자동 생성됩니다. 이미 있는 팀 이름을 입력하면 확인 후 팀장에게 가입 요청이 전달됩니다. 운영자는 승인하지 않고 기록만 확인합니다. 학생은 SSH 키 없이 VM 접속 ID·비밀번호를 정해 Ubuntu VM을 만들 수 있습니다. 생성 후 새 브라우저 SSH 탭에서 자동으로 연결합니다. 팝업이 차단되면 같은 탭에서 비밀번호를 다시 입력할 수 있습니다. 화면 콘솔은 SSH가 고장 났을 때 사용합니다. 팀 Quota는 VM 4개, vCPU 8개, RAM 16GB입니다. Horizon은 관리자용입니다.

## VM 프로그램 자동 설치

### Node.js 보안 기준 (2026-10-23)

Node.js 선택 계열은 24·22를 유지하며, 설치 시 서명된 NodeSource 저장소의 최신 안정 패치를 선택합니다. 최소 버전은 **24.22.1 / 22.23.4**입니다. 수정본이 없거나 설치 결과가 기준에 못 미치면 완료 처리하지 않습니다. React·TypeScript·Firebase의 Node.js 자동 설치에도 같은 검사를 적용합니다. 프로그램 추가 시 기존 Node.js가 취약하거나 지원하지 않는 계열이면 중단하며, 기존 런타임을 임의로 덮어쓰지 않습니다.

이 변경은 새 설치용 코드이며 이미 실행 중인 학생 VM이나 컨테이너를 자동 업데이트하지 않습니다. Node.js 프로세스의 보안 업데이트는 실행 파일 교체 후 해당 서비스를 재시작해야 적용됩니다. 근거: [Node.js 2026-10-21 보안 공지](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases), [24.22.1](https://nodejs.org/en/blog/release/v24.22.1), [22.23.4](https://nodejs.org/en/blog/release/v22.23.4).

설치 옵션은 검증 완료 후 두 포털에 `ops/cloud-portal-software.conf` drop-in을 적용해 활성화합니다. 기본값은 비활성이고, 카탈로그와 직접 API 요청 모두 차단하여 검증 중인 옵션을 노출하지 않습니다.

2026-09-30 운영 두 포털에서 활성화했습니다. 추천 버전 17개 조합의 명령·DB·웹 응답을 검사했고, 다른 버전 17개 조합은 새 VM에서 첫 설치부터 실제 재부팅 후 자동 실행·DB 건강 상태까지 통과했습니다. 전체 개별 버전의 모든 조합을 검증했다는 뜻은 아닙니다. 검사에 사용한 VM은 삭제했습니다.

새 VM의 `함께 설치할 프로그램`에서 프로그램을 여러 개 추가하고 버전과 포트를 고릅니다. 아무것도 선택하지 않으면 기존 Ubuntu·Tailscale 구성입니다. 계열 버전은 해당 계열의 최신 패치, 정확한 버전은 지정한 버전을 설치합니다. 설치 상태는 브라우저 SSH 상단, 접속 정보와 사용 명령은 `설치 안내` 버튼에서 확인합니다. 기존 VM에는 자동으로 적용하지 않습니다.

PostgreSQL·MySQL·MariaDB·MongoDB·Redis·XAMPP DB를 선택하면 서비스 ID와 비밀번호를 생성 단계에서 정합니다. 비밀번호는 8자 이상이며 조합 규칙은 없습니다. 관리용 예약 ID는 제외합니다. 기존 API의 계정 생략 요청은 자동 발급을 유지합니다. 비밀값은 cloud-init 초기 설정을 통해 VM의 root 전용 파일에 저장하며 포털 DB·감사 로그·일반 VM 메타데이터 속성·입력 오류 응답·접속 안내에는 저장하거나 표시하지 않습니다. OpenStack의 초기 설정(user-data)에는 비밀번호가 포함되므로 클라우드 관리자 및 해당 VM의 메타데이터 접근 권한을 가진 사용자를 신뢰해야 합니다. 운영 중인 기존 DB의 비밀번호는 자동으로 바꾸지 않습니다.

새 VM은 `.hushlogin`으로 Ubuntu의 기본 접속 문구를 숨기고 `UCcloud · 내 서버`와 설치한 서비스·포트·짧은 명령만 표시합니다. 설치 중에는 준비 상태를, 완료 후에는 `uccloud nginx -h` 등의 사용 명령을 보여 줍니다. `uccloud` 명령은 해당 VM의 Docker 서비스 명령을 실행하는 단축 도구이며 관리 서버에는 설치하지 않습니다. 기존 VM의 계정·데이터·로그인 화면은 임의로 변경하지 않습니다.

| 역할 | 프로그램 | 선택 버전 |
|---|---|---|
| 개발 환경 | Node.js | 24, 22 (npm 포함) |
| 개발 환경 | Python | 3.14, 3.13, 3.12 |
| 개발 환경 | Java | 21, 17 (OpenJDK·Maven) |
| 웹서버 | Nginx / Apache | 1.30·1.31 / 2.4 |
| DB | PostgreSQL / MySQL / MongoDB | 18·18.6·17·16 / 8.4·9.7 / 8.0·7.0 |
| DB | MariaDB | 11.8, 10.11 |
| 캐시 | Redis | 8.2, 7.4 |
| 실행 도구 | Docker | 29, 28 (Compose 포함) |
| 팀 개발 도구 | Spring Boot / Tomcat·JSP | 3.5.16·4.1.1·4.0.8 / 10.1·11.0·9.0 |
| 팀 개발 도구 | React / TypeScript | 19·18 / 7.0·6.0·5.9 |
| 클라우드 도구 | Firebase CLI | 15, 14 |
| 개발용 묶음 | XAMPP 자체 설치 | Linux 8.2.12 |

Node는 NodeSource의 서명된 저장소, Java와 Python 3.12는 Ubuntu 패키지, Python 3.13·3.14는 uv의 관리형 Python으로 설치합니다. 선택한 Python은 `python`으로 실행하며 OS의 `python3`은 유지합니다. Docker는 공식 서명 저장소에서 선택한 메이저 버전으로 설치합니다. DB·웹서버는 Docker Official Images와 영구 볼륨, 재시작 정책으로 실행하며 Docker를 자동 설치합니다. Docker 그룹에는 사용자를 추가하지 않습니다(`sudo docker`). 기본 웹 포트는 Spring 8080, Apache 8081, Nginx 8082, Tomcat 8083, XAMPP 8084, React 3000이며 변경할 수 있습니다. 포트 충돌은 서버에서 거부합니다. 모든 서비스 포트는 127.0.0.1에만 연결됩니다. DB 비밀번호는 VM에서 무작위 생성하며 포털 DB나 생성 메타데이터에는 보관하지 않습니다. `~/UCcloud-설치안내.txt`와 `/opt/uccloud-services` 설정은 제한된 권한으로 보관합니다.

Spring은 Java·Maven과 빌드된 시작 프로젝트를 학생 계정의 systemd 서비스로 자동 실행합니다. React는 Node.js와 시작 프로젝트를 빌드해 Nginx로 자동 실행하며 개발 서버를 공개하지 않습니다. DB·웹서버·Spring·React·XAMPP는 재부팅 후에도 자동으로 실행됩니다. 설치 완료 표식을 남겨 재부팅마다 재설치하지 않습니다. TypeScript와 Firebase CLI는 Node.js를 자동 설치합니다. Firestore는 VM 안에 설치하는 DB가 아니므로 학생 Google 계정으로 `firebase login --no-localhost` 후 직접 프로젝트를 선택해야 합니다. XAMPP는 공식 설치 파일의 SHA-256을 검증해 `/opt/lampp`에 실제 설치합니다. FTP는 시작하지 않으며, 관리 페이지를 차단하고 DB에 무작위 비밀번호를 설정합니다. XAMPP의 DB는 33306번을 따로 사용합니다. 개발용 묶음이므로 보안이 중요한 운영 서비스에는 개별 최신 패키지가 적합합니다.

설치 대기 화면에는 실제 설치 단계와 경과 시간을 표시합니다. 예상 시간은 선택 개수에 따라 3~8분·5~15분·8~25분 범위로 안내하며, 정확한 남은 시간이나 가짜 진행률을 만들지 않습니다. 다운로드가 느리면 예상 초과 안내를 표시합니다. 실패 후 자동 재시도에서도 최초 시작 시각을 유지합니다.

접속 전에는 설치 여부를 추정하거나 VM 생성 시각부터 시간을 세지 않습니다. SSH 연결 후 VM의 실제 상태 파일을 읽으며, 완료·실패 시 경과 시간은 멈춥니다. 상태 파일이 없거나 읽을 수 없으면 확인 불가로 표시하고 타이머를 중단합니다. VM 접속 ID는 `내 VM → 자세히`와 접속 화면에서 확인할 수 있습니다. VM 비밀번호 보기·자동 재설정 기능은 아직 제공하지 않습니다.

확인된 설치 완료 결과는 포털 DB에 비밀값 없이 저장하고 이후 SSH 접속에서 재조회하지 않습니다. 추가 설치를 시작하거나 백업 복구를 요청하면 저장된 완료 상태를 지우고 실제 상태를 다시 확인합니다. 삭제한 VM의 상태도 지웁니다. 이 상태는 설치 이력이며 서비스의 현재 건강 상태를 뜻하지 않습니다.

프로그램 선택 맨 위의 기본 프리셋은 웹 개발(Node.js·React·PostgreSQL), Python·PostgreSQL, Spring Boot·PostgreSQL 조합입니다. DB ID·비밀번호는 사용자가 입력하고 프로그램별 버전과 포트도 변경할 수 있습니다. `내 VM → 관리 → 프로그램 추가`는 브라우저 SSH를 열고 인증 후 선택 화면을 표시합니다. 추가 설치에는 기존 VM 사용자의 sudo 권한이 필요합니다. VM의 systemd 작업으로 실행해 브라우저를 닫아도 계속 설치합니다. 기존 프로그램의 버전 변경·삭제·재설치는 제공하지 않으며 기존 폴더·서비스·포트와 충돌하면 중단합니다. Compose의 기존 서비스·볼륨·비밀번호·설치 안내를 보존하고 새 서비스만 시작합니다. 수동으로 만든 임의의 설치 환경 전체와 호환된다는 보장은 없습니다.

XAMPP·MySQL·MongoDB는 최소 4GB, DB 2~3개를 같이 고르면 최소 4GB, DB 4개 이상은 최소 8GB VM이 필요합니다. 선택한 조합의 최소 사양은 생성 화면과 서버 검증에서 동일하게 적용합니다.

사진의 Spring Boot `3.6.16`은 공식 릴리스에 없어 `3.5.16`을 제공하며, Spring Boot 4.x도 별도로 선택할 수 있습니다. JSP는 Java 문법으로 웹 페이지를 만드는 기술이며 Tomcat으로 실행합니다. Tomcat은 학생 VM 내부의 host 네트워크를 쓰되 HTTP 연결을 127.0.0.1에 한정하고 종료·AJP 포트는 열지 않습니다. 따라서 JSP와 Spring 모두 같은 localhost DB 주소를 사용할 수 있습니다. XAMPP의 내장 DB는 MariaDB이므로 별도 MySQL이 필요하면 MySQL도 선택하세요.

## 아이디·비밀번호 찾기

로그인 화면의 `아이디 찾기`에서 가입 이메일을 입력하면 아이디를 표시합니다. `비밀번호 찾기`는 가입 이메일로 새 비밀번호를 정하는 HTTPS 주소를 보냅니다. 기존 비밀번호를 메일로 보내지 않습니다. 주소는 30분 동안 한 번만 사용할 수 있고, 비밀번호 변경 시 모든 기존 세션과 재설정 주소를 폐기합니다. 비밀번호 정책은 8자 이상으로 동일합니다.

발신 계정은 `fluction.official@gmail.com`입니다. Gmail 앱 비밀번호는 서버의 root 전용 `/etc/cloud-portal/smtp-password`(600)에만 넣으며 두 포털 서비스의 `LoadCredential`로 전달합니다. 값은 저장소·환경 파일·로그에 넣지 않습니다. 발송은 TLS 인증서를 검증하는 Gmail SMTP 465를 사용합니다. 입력 후 두 포털 서비스를 재시작해야 변경이 적용됩니다. `ops/cloud-portal-mail.conf`는 해당 drop-in 예시이며, `python -m ops.check_mail`은 같은 systemd credential을 주입받아 발신 계정 자신에게만 시험 메일을 보냅니다. SMTP 접수 성공이 수신함 도착을 보장하지는 않으므로 스팸함도 확인하세요.

시장조사 기준: [Stack Overflow 2025 개발자 조사](https://survey.stackoverflow.co/2025/technology), [W3Techs 웹서버 점유율](https://w3techs.com/technologies/comparison/ws-apache,ws-nginx). 서로 다른 분야의 수치를 합친 절대 1~10위가 아니라 학생용 서버에서 활용할 사용량 높은 10개 항목을 선정했습니다. 버전 기준은 [Node 릴리스](https://nodejs.org/en/about/previous-releases), [Python 지원 버전](https://www.python.org/downloads/), [Docker 설치](https://docs.docker.com/engine/install/ubuntu/) 및 각 Docker Official Image의 지원 태그입니다. 조사일: 2026-09-30.

## 접속 방법

학생용 포털 공개 주소는 https://comsv-03.tail5819db.ts.net/ 입니다. 가입할 때 운영자가 전달한 가입 코드가 필요합니다. VM의 `접속` 버튼은 같은 HTTPS 주소의 브라우저 SSH를 엽니다. 학생은 SSH 포워딩이나 키 파일을 설정할 필요가 없습니다. SSH 화면의 `단축키`에서 복사·붙여넣기 키를 바꿀 수 있습니다. 기본값은 Ctrl+C·Ctrl+V이며 글자를 선택하지 않은 Ctrl+C는 명령 중지입니다. 붙여넣기 버튼은 제공하지 않습니다. 안내 페이지의 `사이트를 계속 켜두려면`은 학생 계정의 자동 시작 서비스를 만들며, VM 재부팅 후에도 사이트를 다시 실행합니다. 한 VM에서 기본적으로 웹 서비스 한 개를 설정하는 방식입니다. 맨 아래 빨간 `화면 콘솔` 버튼은 8443 포트의 복구 콘솔을 엽니다. 8443 주소만 직접 열면 VM별 접속 정보가 없어 404가 나옵니다. 로그인 후 `비밀번호 변경`, 로그인 전 `아이디 찾기`·`비밀번호 찾기`를 사용할 수 있습니다.

관리자용 포털은 공개하지 않았습니다. Windows PowerShell에서 다음 SSH 포워딩을 실행하고 유지하세요.

```powershell
ssh -N -L 8088:127.0.0.1:8088 -L 6080:172.29.236.11:6080 user@100.74.103.115
```

브라우저에서 `http://127.0.0.1:8088`에 접속합니다. 포워딩한 6080 포트는 화면 콘솔용이며, 학생의 브라우저 SSH는 공개 HTTPS 포털에서 사용합니다. 관리자 ID는 `admin`입니다. 최초 비밀번호는 컨트롤러의 `/etc/cloud-portal/portal.env`에 `PORTAL_ADMIN_PASSWORD`로 저장되어 있습니다. 컨트롤러에서 `sudo grep '^PORTAL_ADMIN_PASSWORD=' /etc/cloud-portal/portal.env`로 확인하세요. 로그인 후 `운영 기록`에서 가입 코드를 보고 복사할 수 있습니다. 비밀 파일의 내용을 Git에 넣지 마세요.

Tailscale Funnel이 `443 → 127.0.0.1:18080`(학생 포털), `8443 → 127.0.0.1:16080`(복구 콘솔)으로 실행 중입니다. 공용 HTTPS 릴레이에서 두 경로 모두 정상 응답을 확인했습니다. 내부 관리용 `8088`은 공개하지 않습니다. 가입 코드는 공개 포털의 root 전용 `/etc/cloud-portal/public.env`와 관리자 표시용 `/etc/cloud-portal/registration-code`에 보관합니다. 코드를 바꿀 때 두 파일을 함께 수정하고 두 포털 서비스를 재시작해야 합니다. 비밀 코드는 공개 문서나 저장소에 기록하지 않습니다. 학생 VM의 웹 서비스는 학생 팀의 Tailscale 계정에서 Funnel을 설정해 공개하며 `/guide/funnel`에 절차가 있습니다.

## 구성

- 소스: `/opt/cloud-portal`
- 비밀 설정: `/etc/cloud-portal/portal.env`, `/etc/cloud-portal/public.env` (root 전용, 600)
- DB: `/var/lib/cloud-portal/portal.db` (서비스 계정 전용, 600)
- DB 백업: `cloud-portal-db-backup.timer`가 매일 온라인 백업을 만들고 임시 DB로 복원 검사. 파일은 `/var/backups/uccloud-portal`에 root 전용으로 저장
- VM 백업·복구: VM을 끈 뒤 `관리 → 백업·복구`에서 요청합니다. 백업 파일은 원본 VM과 다른 컴퓨트 노드에 저장하고 SHA-256으로 확인합니다. 복구는 선택한 백업 시점으로 기존 VM을 바로 되돌립니다. 노드당 백업 상한은 200GiB이며 여유 공간 100GiB를 남깁니다. VM당 검증된 최신 백업 1개를 유지합니다. `cloud-portal-vm-backup.timer`가 대기 작업을 30초 간격으로 처리합니다. 파일은 각 컴퓨트의 root 전용 `/var/backups/uccloud-vms`에 있습니다.
- 사이트 상태: VM의 `관리 → 사이트 상태`에서 팀의 Tailscale Funnel 공개 주소를 입력해 직접 확인합니다. 마지막 확인 결과와 주소를 저장하지만 자동 감시는 하지 않습니다. 공개 `https://<장치>.<팀>.ts.net` 주소의 첫 화면만 확인하며, 내부 IP와 다른 도메인은 점검하지 않습니다.
- 별도 노드 백업: `ops/backup_crypto.py`와 `ops/replicate_portal_backup.py`를 준비했습니다. 별도 물리 노드인 `comsv-01`로 암호화 전송한 뒤 다시 내려받아 원본과 동일하게 복원되는 일회성 시험을 통과했습니다. 성공한 로컬 백업 다음에만 복제하는 `ops/cloud-portal-db-replica.service`와 `ops/cloud-portal-db-backup-on-success.conf`도 준비·문법 검증했습니다. 시험 파일과 키는 제거했고, 복구 개인키의 보관 위치가 정해지기 전까지 정기 복제는 설치·활성화하지 않았습니다. 현재 복구 가능한 운영 백업은 여전히 컨트롤러 디스크에만 있습니다.
- 파일 무결성 검사: `dailyaidecheck.timer`가 매일 실행. 2026-09-24 첫 기준 DB를 만들었고, 저우선순위 설정은 `ops/dailyaidecheck-low-priority.conf`에 보관. 결과는 `/var/log/aide/aide.log`에서 확인하고 변경된 기준은 검토 후에만 수동 반영(`COPYNEWDB=no`)
- 서비스: `cloud-portal.service`(관리자), `cloud-portal-public.service`(학생), `cloud-portal-console-gateway.service`(VM 콘솔)
- 클라우드 상태 점검: `cloud-portal-cloud-health.timer`가 5분마다 인증·Quota를 읽기 전용으로 확인하고, 문제와 복구 시점에만 운영 기록을 남김. 자동 재시작은 하지 않음
- 브라우저 SSH: 같은 HTTPS 포털의 WebSocket으로 연결. VM 비밀번호와 셸 출력은 저장하지 않음. 팀 소유권·CSRF·Origin 검사와 VM 호스트 키 고정 적용
- 네트워크: 컨트롤러 `172.141.0.231/32`만 팀 VM의 SSH 포트로 허용. 팀별 `/24` 경로는 `cloud-portal-ssh-routes.timer`가 공유 라우터로 동기화
- 화면 콘솔: SSH가 고장 나도 사용할 수 있는 QEMU 복구 경로. 브라우저 상단 입력칸으로 영문 명령을 전달할 수 있으나 화면의 텍스트 복사는 제한됨
- Tailscale 첫 연결: 브라우저 SSH에서 `sudo tailscale up`을 실행하고 표시된 로그인 주소를 PC 새 탭에서 열어 승인. 일회용 인증 키 입력은 화면 콘솔의 대체 수단
- OpenStack 연동: 관리자 계정은 팀 승인·Quota에만 사용, VM 작업은 각 팀 Project의 `portal-service` member 권한으로 수행
- 팀 네트워크: 독립 서브넷, 기존 `router1`의 외부 경로 공유
- VM 초기 비밀번호: 포털 DB에는 저장하지 않고, SHA-512-crypt 해시만 cloud-init 사용자 데이터로 전달. 신규 VM은 SSH 비밀번호 로그인이 가능하지만 인터넷에 22번 포트를 공개하지 않음
- 신규 Ubuntu VM: 첫 부팅에 Tailscale 설치 서비스가 백그라운드로 시작하며 일시적인 설치 실패는 자동 재시도. SSH 접속이나 cloud-init 완료가 설치 완료를 뜻하지는 않음. 팀 계정 로그인·Funnel 공개는 학생이 선택하며 운영자 인증 키는 VM에 넣지 않음
- 학생 안내: `/guide/funnel` (실제 화면 사진은 확보 전까지 빈 자리로 표시)

## 운영 확인

### 원격 AI 연결

계정 비밀번호 변경·재설정과 VM 삭제·백업 복구 요청 시 관련 AI 연결을 폐기합니다. 기본 공개 API 주소는 `https://comsv-03.tail5819db.ts.net/api/agent/v1`이며 도메인 변경 시 `PORTAL_AGENT_PUBLIC_ORIGIN`을 두 서비스에 같은 HTTPS origin으로 설정합니다.

`내 VM → AI 연결`에서 평소처럼 VM 비밀번호로 웹 SSH에 연결하면 안내가 자동 생성됩니다. 이미 연결했다면 터미널 상단의 `AI 연결`을 누릅니다. `안내 복사` 후 **외부 HTTPS 요청을 할 수 있는 코딩 에이전트**에 붙여넣고 작업을 요청합니다. 일반 채팅 AI나 네트워크가 차단된 에이전트는 사용할 수 없습니다. 추가 연결 프로그램·학생 Tailscale 로그인·외부 SSH 포트 개방은 필요 없습니다.

- 기본 4시간, 1시간/하루 선택. 안내를 다시 만들면 해당 사용자의 같은 VM 이전 연결이 해제됩니다. 브라우저를 닫아도 연결은 만료까지 유지되며, `연결 해제`는 실행 중인 API 작업도 중단합니다. 이미 VM에서 별도 서비스로 실행한 프로그램까지 종료하는 기능은 아닙니다.
- HTTPS API `/api/agent/v1`: context, instructions, jobs(실행·결과·취소), files(목록·읽기·쓰기), disconnect. 요청에 `Authorization: Bearer <임시 키>` 필요. 키에는 사용자·프로젝트·VM·접속 ID를 서버에서 고정하며 임의 SSH 주소를 받지 않습니다.
- 웹 SSH의 기존 연결을 통해 컨트롤러 출발지·만료 시간이 제한된 전용 SSH 키를 등록합니다. API 키는 해시만, 내부 SSH 개인키·sudo 인증용 VM 비밀번호·작업 명령·출력은 systemd `LoadCredential`로 공급한 master key로 암호화해 저장합니다. 비밀번호는 sudo 확인에 성공한 연결에만 유효기간 동안 보관하고 만료·해제·계정 상태/팀 변경 시 지웁니다. `/etc/cloud-portal/agent-master.key`는 root 600이며 두 포털 서비스에 `ops/cloud-portal-agent.conf`를 적용합니다. 암호화된 DB 백업에도 자격 정보가 포함될 수 있어 master key는 백업과 별도로 보호해야 합니다.
- 각 명령은 별도 비대화형 bash: cwd·환경을 매번 지정합니다. 관리자 작업은 `/jobs`에 `sudo:true`를 넣고 명령에는 sudo를 중복하지 않습니다. `/context`의 `sudo_available`을 먼저 확인합니다. UCcloud가 비밀번호를 SSH stdin으로 전달해 sudo를 인증하며 에이전트에게 비밀번호를 주지 않습니다. 기존 sudo 설정·루트 SSH·네트워크 허용 범위는 변경하지 않습니다. 장기 서비스는 systemd로 실행합니다. 파일 API는 홈 폴더만, 심볼릭 링크·하드링크·특수 파일·`.ssh` 접근을 거부합니다. 기존 파일을 읽고 사용자 변경을 보존해야 합니다.
- 사용자당 동시 작업 2개/플랫폼 8개, 연결당 작업 100개·파일 전송 100MiB, 요청 분당 120개, 작업 최대 30분, 출력 256KiB, 파일 20MiB·쓰기 청크 6KiB. 작업 결과 보관 24시간. request_id로 중복 실행을 방지하며 서버 재시작 후 작업을 자동 재실행하지 않습니다.
- 임시 키는 **해당 VM 사용자의 sudo 권한을 포함한 작업 권한**입니다. 복사한 안내를 타인에게 공유하거나 소스·Git·로그에 남기지 마세요. 감사 로그에는 작업 ID·종류·완료 상태만 기록하고 명령·출력·파일 내용은 남기지 않습니다. 만료·폐기 뒤 개인키는 지우며 VM의 공개키는 OpenSSH 만료 제한으로 사용이 차단됩니다.
- 설치 검증: `tests/test_agent_access.py`, `tests/test_agent_ui.js`, 운영 공개 HTTPS와 기존 test VM을 사용하는 `ops/verify_agent_live.js`. master key가 없으면 발급을 거부합니다. DB 복구 후 기존 연결을 해제·재발급할 수 있으며 복원된 작업을 재실행하면 안 됩니다.

### 웹 SSH 파일 업로드

VM에 접속한 뒤 터미널 아래 `파일 올리기`에 파일을 끌어다 놓거나 `파일 선택`을 누릅니다. VM 로그인 사용자의 `~/uploads` 폴더에 저장됩니다. 파일당 100MB, 접속당 총 500MB·200개이며 같은 이름을 자동 덮어쓰지 않습니다. 진행률·취소·오류 안내를 제공하고, 업로드 중 로그인 만료와 팀 변경도 검사합니다. 파일을 포털 서버에 저장하거나 자동 실행·공개하지 않습니다. 완료 전 파일은 숨김 임시 파일로 기록하며 정상 취소·연결 종료 때 정리합니다. VM 연결 자체가 끊어지면 숨김 미완료 파일이 남을 수 있습니다.

### 관리형 웹사이트 공개

웹 SSH 아래 `웹사이트 공개하기`에 실행 중인 HTTP 서비스의 포트를 입력합니다. UCcloud가 공개 전용 Tailscale 연결과 HTTPS Funnel을 설정하고 주소를 보여 줍니다. 학생의 Tailscale 가입·로그인은 필요 없습니다. 사이트 실행 자체는 별도이며, 파일 업로드만으로 프로그램을 실행하지 않습니다. 안내는 UCcloud의 PC 접속·파일 업로드·공개 화면 사진을 사용하며 외부 계정 가입 설명과 빈 사진 자리는 제거했습니다.

공개 전용 계정은 `uccloud.offcial@gmail.com` / `tail2d25b3.ts.net`입니다. `ops/public-tailnet-policy.json`으로 장치 간 연결·Tailscale SSH를 차단하고 공개 태그에만 Funnel을 허용합니다. 관리망에 적용하지 않습니다. 다른 계정에 이미 연결된 VM은 변경을 거부합니다. 기존 공개 주소가 있는 경우 443 포트의 사이트 대상은 새로 입력한 포트로 변경됩니다. 공개 끄기는 외부 공개만 중단하며 VM·사이트 프로그램·Tailscale 등록은 유지합니다.

OAuth는 Auth Keys Write, Policy File/Core/Posture Attributes Read와 `tag:uccloud-public`에 제한합니다. root 전용 `/etc/cloud-portal/public-oauth.json`(600)을 systemd `LoadCredential`로 포털에 전달합니다. 원본 OAuth 비밀값은 VM·브라우저·소스·명령 인수·로그로 전송하지 않습니다. VM에는 10분 유효 단일 사용 키만 stdin으로 전달하여 `/run`의 root 전용 임시 파일을 거쳐 등록하고 제거합니다. VM 비밀번호는 연결 세션의 메모리에서만 sudo 인증에 사용하고 종료 시 해제합니다. 등록 실패 시 사용하지 않은 키를 폐기합니다.

요청마다 로그인·프로젝트·VM 소유권을 확인하고 입력 포트는 정수 1~65535(22 제외)로 제한합니다. VM의 127.0.0.1 HTTP 응답을 확인한 뒤 공개하며 팀 간 VM 조작·임의 URL 프록시는 제공하지 않습니다. 공개 변경은 VM별 프로세스 간 잠금과 사용자별 10분 6회 제한을 사용하며 비밀값 없는 운영 기록만 남깁니다. 단계·경과 시간·한국어 오류·주소 복사·중단을 제공하고 주소는 내 VM 사이트 상태에 저장합니다. 첫 공용 DNS 반영은 최대 10분 걸릴 수 있습니다. 연결 창을 닫으면 진행 작업이 중단될 수 있으므로 상태를 재확인합니다.

### 관리자 팬 관리

관리자 전용 포털의 `운영 기록 → 팬 관리`에서 다섯 서버의 팬 속도·CPU 온도를 확인하고 최소 팬 속도를 바꿀 수 있습니다. 기본값은 `자동 (권장)`(추가 최소 속도 0%)이며 팬을 끄는 설정이 아닙니다. 현재 필요한 최저 속도는 대체로 6%입니다. 최소 20%를 선택해도 온도에 따라 더 빠르게 동작할 수 있습니다. 개별 팬 고정, 음수 보정, 과열 보호 해제는 제공하지 않습니다.

Python 코드는 `ops/ilo_control.py`입니다. 운영본은 `/usr/local/lib/uccloud/ilo_control.py`에 root 소유로 설치합니다. 컨트롤러에는 컴퓨트 네 대의 iLO 정보, `comsv-01`에는 컨트롤러 iLO 정보를 root 전용 `/etc/uccloud-fans/ilo.json`(600)에 보관합니다. 공유 NIC의 자기 관리 포트 접속 제약 때문에 경로를 나눴습니다. 비밀번호는 소스·웹 응답·로그·명령 인수에 넣지 않습니다. 최초 인증서 고정 후 인증서가 바뀌면 연결을 중단합니다.

`uccloud-fans.service`는 고정된 팬 제어 요청만 받는 전용 Unix 소켓을 제공합니다. 포털은 root로 실행하지 않으며 공개 포털에는 `/api/admin/fans` 경로가 없습니다. 관리자 권한·CSRF·입력 범위·이전 설정 값·온도/팬 경고를 검사하고 변경 요청·결과를 운영 기록에 남깁니다. iLO의 설정 유지와 systemd 자동 시작을 사용하며 재부팅 때 임의로 사용자의 설정을 덮어쓰지 않습니다. 자동으로 되돌리려면 최소 속도에서 `자동 (권장)`을 선택합니다.

컨트롤러에서 웹 없이 실행하려면 `sudo python3 /usr/local/lib/uccloud/ilo_control.py status`로 컴퓨트 상태를 확인하고 `set compute-1 20 --expected 0`처럼 현재 최소 속도를 함께 지정합니다. 컨트롤러의 iLO만 `comsv-01`에서 같은 코드의 `set controller ...`로 제어합니다. 0으로 적용하면 자동 냉각으로 돌아갑니다.

```bash
sudo systemctl status cloud-portal --no-pager
sudo systemctl status cloud-portal-public cloud-portal-console-gateway --no-pager
sudo tailscale funnel status
curl -fsS http://127.0.0.1:8088/api/health
sudo journalctl -u cloud-portal -n 100 --no-pager
sudo systemctl status cloud-portal-db-backup.timer --no-pager
sudo systemctl status cloud-portal-cloud-health.timer --no-pager
sudo systemctl status cloud-portal-vm-backup.timer --no-pager
```

DB 백업 상태는 `sudo journalctl -u cloud-portal-db-backup.service -n 20 --no-pager`에서 확인합니다. 복원 가능성은 `sudo python3 /opt/cloud-portal/ops/backup_portal_db.py --verify /var/backups/uccloud-portal/<백업파일명>`으로 다시 검사할 수 있습니다. 이 백업은 컨트롤러와 같은 디스크에 있으므로 디스크 고장에 대비한 별도 보관이 필요합니다.

VM 백업 상태는 `sudo journalctl -u cloud-portal-vm-backup.service -n 50 --no-pager`로 확인합니다. VM 백업은 다른 노드에 있지만 별도 저장 장치나 오프사이트 복제는 아닙니다. 원본 노드와 백업 노드가 함께 고장 나면 복구할 수 없습니다. 복구 직전 상태는 자동으로 별도 보관하지 않으므로 복구 버튼을 누르기 전에 반드시 백업 시점을 확인하세요.

별도 노드에 암호화 백업을 저장하려면 운영자 PC에 복구 개인키와 백업 서명 확인용 공개키를 두고, 컨트롤러에는 복구 공개키와 서명 개인키를 설치해야 합니다. `comsv-01`에는 암호화·서명된 백업만 둡니다. PC 분실까지 대비하려면 복구 개인키의 독립적인 안전한 사본도 필요합니다. 이 복제는 포털 계정 DB만 보호하며 OpenStack DB·VM 디스크 백업이나 컨트롤러 HA를 대신하지 않습니다.

AIDE의 첫 기준 DB는 설정 당시 파일 상태를 기록할 뿐, 그 이전에 침해가 없었다는 증거는 아닙니다. 현재 메일 알림은 설정돼 있지 않으므로 운영자가 검사 로그를 확인해야 합니다.

Keystone에서 간헐적으로 503이 나면 `controller1-keystone-container-301f3fef`의 `keystone-wsgi-public` 로그, 같은 시각 HAProxy의 Keystone HTTP 상태 코드, Galera 상태를 함께 확인합니다. 2026-09-24에는 한 uWSGI 작업 프로세스에서 SQLAlchemy 연결 풀 대기 오류가 반복됐습니다. 이후 5분 점검 시각에 DB 연결 종료 경고가 남았지만 해당 인증 요청은 HAProxy에서 200·201로 완료됐고, 12:20 이후 확인한 Keystone 경로에는 5xx가 없었습니다. 이 경고만으로 서비스 재시작을 결정하지 마세요. 이 환경에서 `systemctl reload keystone-wsgi-public`은 서비스를 종료했으므로 사용하지 마세요. 실제 장애로 재시작이 필요하다면 영향 범위를 확인한 뒤 `systemctl restart`하고 인증 API·VM 생성 경로를 다시 검증해야 합니다. 재시작은 원인 해결을 보장하지 않습니다.

기능 검증과 보안 검토 결과 및 남은 위험은 [SECURITY.md](SECURITY.md)에 기록했습니다.

