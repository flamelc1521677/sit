# OFFSAAS 接口自动化测试

密码、本地状态、日志和固定证件图片现已提供加密备份，见 `backups/README.md`。原始文件仍忽略上传；解密密钥仅保存在本机 `.secrets/`。

在 VS Code 中打开 api-tests 文件夹，按场景编号选择运行入口。

## 从 GitHub 获取项目

克隆后在项目根目录创建 Python 虚拟环境，并安装 `requirements.txt`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

默认只执行本地规则验证，不提交业务请求。账号密码、本机 Windows 用户环境、`.state/` 中的号码和业务记录、`artifacts/` 日志及固定测试图片均不上传。其他机器执行接口场景前，需配置登录/数据库/审批凭据及测试图片；执行前由维护者分配未占用的号码区间并初始化独立状态，不能将默认起始序号当作可用号码。本机现有状态与图片保持原样。

## 文件层级

```text
api-tests/
├── README.md                   使用说明
├── requirements.txt            依赖
├── pytest.ini                  默认运行范围与导入路径
├── .vscode/
│   ├── launch.json             按场景排序的 F5 配置
│   └── settings.json           Python 与资源管理器设置
├── common/                     公共代码，不直接运行
│   ├── sso_login.py            验证码、密码加密、SSO 登录
│   ├── admin_client.py         OAuth 授权与后台登录
│   ├── phone_sequence.py       手机号递增、持久化与并发锁
│   ├── identity_sequence.py    证件号日期按日递增
│   ├── identity_client.py      OCR、保存与回查
│   └── state_store.py          最新新增用户记录
├── tests/
│   ├── conftest.py             共用 Requests Session
│   ├── 01_login/
│   │   └── test_sso_login.py   场景 01：SSO 登录
│   ├── 02_user_management/
│   │   └── test_add_user.py    场景 02：登录后新增用户
│   ├── 03_identity_verification/
│   │   └── test_identity_ocr.py 身份证 OCR 与保存
│   └── 04_local_checks/
│       └── test_phone_sequence.py  场景 04：本地号码验证
└── .state/
    ├── phone.json              下次手机号，必须保留
    └── phone.lock              分配锁
```

环境目录和缓存在资源管理器中隐藏，磁盘文件仍保留。手机号状态保持可见。

## 场景顺序与预期

| 顺序 | 场景 | 预期 |
| --- | --- | --- |
| 01 | SSO 登录 | HTTP 200、code 200、msg result.success |
| 02 | 用户管理 / 新增用户 | HTTP 200、code 0、msg 线下用户建档成功。 |
| 03 | 身份证 OCR 与保存 | OCR 成功、保存 code 0、回查证件号变化且姓名不变 |
| 04 | 本地验证 / 手机号与证件日期 | 前导零、持久化、并发唯一性、损坏状态校验 |

场景 02 内置完整的 SSO 登录和 offadmin OAuth 回调，可以单独运行。

## 运行方式

现有 .venv 已安装依赖。账号密码通过环境变量提供，不写入脚本。

```powershell
$env:SSO_USERNAME = 'ob4-mx'
$credential = Read-Host '输入登录密码' -AsSecureString
$env:SSO_PASSWORD = [System.Net.NetworkCredential]::new('', $credential).Password

# 01：登录
.\.venv\Scripts\python.exe -m pytest tests/01_login -v -s
# 02：登录后新增一个用户
.\.venv\Scripts\python.exe -m pytest tests/02_user_management -v -s
# 04：仅本地验证
.\.venv\Scripts\python.exe -m pytest tests/04_local_checks -v
# 查看所有场景，不执行
.\.venv\Scripts\python.exe -m pytest tests --collect-only -q

Remove-Item Env:SSO_PASSWORD
```

直接执行 pytest 默认只运行本地验证。指定 tests 会执行所有场景，包含真实新增用户。

VS Code 左侧“运行和调试”下拉框：01 · SSO 登录 → 02 · 用户管理 / 新增用户 → 03 · 个人信息 / 身份证 OCR 与保存 → 04 · 本地验证 / 手机号与证件日期。选择场景后按 F5，调试控制台展示步骤日志。VS Code 需继承账号环境变量，可从设置好变量的终端使用 code . 启动；已运行的 VS Code 可能需重启才能继承新环境。

验证码默认使用本地 OCR；识别错误时测试失败，尚未发送新增请求。人工备用模式设置 SSO_CAPTCHA_MODE=manual，在交互终端加 -s 运行，验证码图片会打开，有效期 60 秒。VS Code 调试控制台使用 OCR 模式。

## 新增用户参数与状态

### 10 · 复借 / 指定用户 → 试算 → 提交一次

该笔复借已按用户要求继续至放款成功：ob5-mx 审批人审单 a78a2accc58544d2adbbc373776adb34，通过接口 code=0 / 操作成功；ob4-mx 处理放款单 ec7f225c02474c15b841cb8b035dd2c8，状态 2 → 1，返回 code=0 / 修改成功。合同 C1320261016142042Oc0Mu。审批状态、原因、额度、人工日志及放款流水、合同/借款状态同步数据库校验均通过；续跑 2 passed in 23.49s。未新增用户、未重复提交借款，日志已追加至 artifacts/repeat_loan_862_20261016_01.log。

仅在明确需要新一笔借款时选择 VS Code 的 10 入口，填写目标用户 ID 和本次唯一申请标识。同一笔复借重复运行时必须沿用原标识；不能为了重试而更换标识。脚本要求目标与最新用户一致、前次提交 db_verified、数据库主单与本地历史完全匹配，随后检查服务端借款资格，再试算和提交。未知提交结果会阻止复借。旧借款记录保存在 previous_loans，当前借款供后续人审与放款按主单关联；默认 05/06/09 仍禁止已有用户自动重复申请。

2026-10-16：用户 862 / 0658900209 复借申请 user862-repeat-20261016-01 已成功提交，产品 001412、金额 2200、3 期、每期 7 天，试算实到 2178。新主单 95ed2c5ccaac4c4791acd4c0989c0d32，主单、分期计划和收款卡数据库校验通过，实测 1 passed in 20.05s。本次仅提交新借款，未操作人审或放款。日志：artifacts/repeat_loan_862_20261016_01.log。复借防重及相关本地验证 69 passed。

上述用户 862 的流程已续跑完成：经用户明确授权，改用 ob5-mx 审批同一人审单，返回 code=0 / 操作成功；随后 ob4-mx 将放款单 c79fd9ac22dc4ebab7b91e40a8ced1fb 从状态 2 审批为 1，返回 code=0 / 修改成功。合同 C1320261016135550kv46K；审批日志、放款流水、合同及借款状态同步数据库校验全部通过，续跑结果 2 passed in 38.73s。未重新新增用户或重复提交借款，下一手机号为 0658900210。完整续跑日志已追加至同一文件。

2026-10-16 最新实测：用户 862 / 0658900209 使用 ob4-mx 的配置 56 建档，身份证日期段 750107、银行卡尾号 0007 保存及数据库校验通过。产品 001412 / 2200 / 3 期 / 每期 7 天试算并提交成功，实到金额 2178。人审单 4ffcac13219742f3bf0f0ac726a21d79 由 ob4-mx 发起通过请求时，服务端仍返回 code=500“不能审批本人发起的放款申请，请联系其他审批人处理”；数据库确认待审 status=0、version=0，未执行放款审批。员工归属变化不能替代服务端审批权限校验。完整日志见 artifacts/full_flow_20261016_0209.log；续跑只处理该用户剩余步骤，不重新新增或重复借款。

用户列表可能把 WhatsApp 返回为 `mx-1wp-15666226611`。回查仅接受固定配置的纯号码或该完整显示值，并核对归属账号；数据库仍校验实际保存的纯号码。历史用户按原状态文件回查。

POST https://saas-offadmin.suxi-test.com/user/user/offline/addUser，表单编码。

| 参数 | 值 |
| --- | --- |
| employeeLineConfigId | 56 |
| employeeLine | 15666226611 |
| registrationType | h5 |
| phone | 10 位字符串，按状态文件递增 |

新增配置统一保存在 `config/user_creation.json`，固定选择 `ob4-mx` 的员工 WhatsApp `mx-1wp`（15666226611 / 配置 56）。新增前先查询数据库校验账号归属和启用状态，异常时停止，不消耗手机号；新增后校验实际落库归属。VS Code 的 02、00、06、09 均调用同一份新增脚本，因此使用相同配置。历史用户仍按状态文件中原有的员工 WhatsApp 回查。人审固定使用独立账号：ob4-mx 发起的借款由 ob5-mx 审批；员工 WhatsApp 归属不改变该规则。

初始号码 0658900201，整理时下次号码为 **0658900204**。发送新增请求前先持久化下一号码；失败或超时不回退、不自动重发。登录失败不消耗号码。不要删除 .state/phone.json，否则序号会重新开始。本机并发共用同一个状态文件；多机器需统一号码分配服务。

可选环境变量：SSO_BASE_URL、ADMIN_BASE_URL、SSO_DEVICE_ID、SSO_CLIENT_ID、SSO_CAPTCHA_MODE、PHONE_STATE_FILE（建议绝对路径）。不打印密码或响应令牌，请勿使用 --showlocals。

## 实测记录（2026-10-10）

- 0658900201：已占用，接口拒绝，未强制新增。
- 0658900202：建档成功，修正断言为实际完整文案。
- 0658900203：建档成功，1 passed in 8.65s。
- 整理目录时仅验证收集与本地号码规则，不新增用户。


## 场景 03：固定身份证图片 OCR 与个人信息保存

VS Code 选择 **03 · 个人信息 / 身份证 OCR 与保存**，按 F5。终端单独执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/03_identity_verification -v -s
```

流程：登录后台 → 从最新成功新增记录定位唯一用户 → 上传固定正面图片 → 检查 OCR 姓名与证件号 → 修改证件号日期段 → 保存 → 回查核对。不会自动新增用户；先执行场景 02 即可更新目标。

- 固定图片：`assets/identity_front.png`，从本次用户附件复制，未修改图片。
- 上传：POST `/user/user/identity/uploadOcr`，multipart 字段 file、uuid、appOwnership、cardType=ocrFort、ocrChannel（从用户详情读取）、i18n=zh_CN。
- 保存：POST `/user/user/saveIdentityVerification`，multipart 表单，证件号字段为 aadhaarNo，保留 OCR 姓名、生日、性别和地址等个人信息。仅证件号码中的日期段按用户要求改变，不修改图片及 OCR 返回的生日。
- 回查：POST `/user/user/identity/ocrInfo`。保存成功预期 code=0，实测 msg=请求成功。当前接口不回显保存证件号且回查返回加密值，因此只能验证存储值变化及姓名不变，不能通过该回查直接证明完整明文完全相同。若未来接口返回明文，脚本会严格与提交号码比较。

最新成功新增用户记录保存为 `.state/latest_user.json`。场景 02 在建档 code=0 后立即记录手机号及 IM；场景 03 按这两个条件查唯一用户，并校验详情 UUID 与应用归属。初始记录已按此前实测成功用户 `0658900203` 初始化，不取后台所有用户中最大 ID，也不从手机号序号反推用户。

日期状态为 `.state/identity_date.json`：首次 1975-01-01，对应 `MAPA750101HMCRND05`，之后 `MAPA750102HMCRND05`、`MAPA750103HMCRND05`……用真实日历加一天，支持月底、年底和闰年；达到 2075 年前停止，避免两位年份重复。OCR 成功后、保存请求发送前才分配日期；保存失败或超时不回退，不自动重发。上传 OCR 本身也可能持久化图片及识别信息，即使后续保存失败也应查看目标用户状态。

`.state/last_identity_result.json` 记录最近一次保存摘要与回查是否完成。不要删除日期状态文件，否则会从 750101 重新开始。可用 `IDENTITY_IMAGE`、`IDENTITY_STATE_FILE` 指定图片与日期状态的绝对路径。

本次实测：用户 ID 733 / 手机号 0658900203，上传 OCR 成功，保存 `MAPA750101HMCRND05` 返回 code=0、msg=请求成功。初版测试因假设保存响应包含证件号而失败；断言已修正，随后只做回查验证通过，未重复保存。下次日期段为 **750102**，新增用户手机号仍为 **0658900204**。


## 场景 03 追加：银行卡保存（身份证成功后自动继续）

同一个 `tests/03_identity_verification/test_identity_ocr.py` 已包含完整流程：身份证上传 OCR → 修改证件号 → 保存与回查 → 银行卡保存与回查。VS Code 场景名称仍为“03 · 个人信息 / 身份证 OCR 与保存”。身份证步骤失败时不执行银行卡步骤，也不消耗卡号。

银行卡接口为 POST `/user/user/saveBankCardInfo`，接收 JSON 数组。每条记录含 uuid、appOwnership、bankNameId、bankCardNum、selectedFlag 等字段。bankNameId 固定为字符串 `37006`，bankCardNum 首次为字符串 `1254112541000002`，随后每次加 1。已有银行卡时更新第一张卡并保留其 ID 和标志，其余卡完整保留；没有银行卡时新建一张默认卡。回查详情严格核对银行和完整卡号，检查其他卡记录未被修改。

卡号持久化状态：`.state/bank_card.json`；可通过 `BANK_CARD_STATE_FILE` 指定绝对路径。发送保存请求前即保存下一卡号，失败或超时不回退、不自动重发。不要删除状态文件。保存摘要位于 `.state/last_bank_result.json`，日志仅显示卡号尾号。

公共模块：`common/bank_client.py` 负责银行卡保存与回查，`common/bank_sequence.py` 负责 16 位字符串递增与锁。本地验证场景已加入卡号持久化、并发唯一性、状态异常停止、保留其他银行卡和详情解析测试。

本次在用户 735 / 手机号 0658900204 已保存成功的身份证之后，单独实测新增银行卡步骤：卡号 1254112541000002、银行 37006，响应 code=0、msg=修改成功。，回查通过。未重复上传和保存身份证。下次卡号为 **1254112541000003**；下次证件日期段仍为 **750103**，下次新增手机号仍为 **0658900205**。


## 最新同步记录与完整运行入口（2026-10-10）

VS Code 的脚本已保存到磁盘，场景 03 已包含身份证和银行卡保存。运行和调试中新增 **00 · 完整流程 / 新增用户 → 身份证 → 银行卡**，选择后按 F5，即依次运行场景 02、场景 03。使用 -x：新增失败时立即停止，不会继续修改之前的用户。场景 03 内部也只有身份证保存与回查通过后，才保存银行卡。此入口与实测使用的两个测试脚本完全一致。

最近一次完整实测：手机号 0658900205，用户 ID 736，证件号 MAPA750103HMCRND05，银行 37006，卡号 1254112541000003。两个场景通过，耗时 30.69 秒。

同步时下次数据：手机号 **0658900206**、证件号 **MAPA750104HMCRND05**、银行卡 **1254112541000004**。运行时始终从 .state 状态文件读取；上面数字仅为此时快照，不写死在测试里。

本次同步仅更新 VS Code 入口、说明并校验测试收集，不再次新增用户，不消耗任何序号。


## SIT MySQL 公共连接与强制数据库校验（2026-10-12）

所有接口场景已加入数据库校验。公共连接为 `common/database.py`，业务断言为 `common/database_checks.py`。测试自动建立、关闭连接，使用参数化 SELECT；连接会话设为只读，校验失败即测试失败，不会通过数据库修改数据。

默认地址 sit-db01.suxi-test.com:3306、用户名 user_readwrite。密码从环境变量 SIT_DB_PASSWORD 读取，不保存到源码。VS Code 按 F5 运行接口场景时自动读取 SIT_DB_PASSWORD，不再弹出数据库密码框。

终端配置：

```powershell
$dbSecret = Read-Host 'SIT MySQL 密码' -AsSecureString
$env:SIT_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $dbSecret).Password
# SSO_USERNAME、SSO_PASSWORD 仍按前文配置
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests/02_user_management tests/03_identity_verification -v -s -x
Remove-Item Env:SIT_DB_PASSWORD
```

可覆盖 SIT_DB_HOST、SIT_DB_PORT、SIT_DB_USER、SIT_DB_BUSINESS_SCHEMA（默认 off_biz_mex）、SIT_DB_ADMIN_SCHEMA（默认 off_saas_saas）。

| 场景 | 数据库校验 |
| --- | --- |
| 01 登录 | off_saas_saas.sys_user：账号唯一存在、STATUS=0、DEL_FLAG=0。未将不存在的 SSO 日志当作登录持久化证据 |
| 02 新增用户 | off_biz_mex.t_mgt_user：UUID+应用归属唯一、ID、手机号密文与接口一致、脱敏手机号、员工 IM；sys_user_line_config：配置 61 的 IM 和启用状态 |
| 03 身份证保存 | t_mgt_user_aadhaar_card_info：记录归属、证件号存储值、脱敏值、OCR 姓名、正面图片。接口返回密文时与 DB 密文比较；返回明文时严格比较请求号码，并校验本次保存前后 DB 密文发生变化 |
| 03 银行卡保存 | t_sys_bank_card：记录 ID+UUID+应用归属、银行 37006、卡号密文、脱敏卡号、默认卡标志 |

数据库身份证、手机号及银行卡并非完整明文；字段 ID_CARD_ENCRYPT、BANK_ENCRYPT 实际是脱敏值，不能用于证明中间全部数字相同。精确明文由接口回查校验；SQL 用于验证相同用户和卡记录的实际落库及密文一致/更新。字段映射已按真实表结构确认，未猜测 registrationType 的数据库映射。

业务写入可能有短暂延迟，SQL 断言仅做最多 10 秒的只读轮询，不重发新增或保存接口。

后续新增接口场景必须使用 database_checks，并在业务步骤后增加 SQL 断言。tests/conftest.py 自动检查：接口场景通过但没有任何数据库断言时，会报错。仅 tests/04_local_checks 的纯本地验证免于数据库连接。维护要求已写入本目录 AGENTS.md。


## 数据库密码自动读取（2026-10-12）

已将 SIT_DB_PASSWORD 配置为当前 Windows 用户的持久环境变量，VS Code 的运行配置已移除密码输入框。公共连接优先读取当前进程环境变量；若正在运行的 VS Code 尚未继承新变量，则直接读取当前 Windows 用户的同名环境变量，因此当前窗口也无需重启即可使用。密码未写入项目文件或日志。已验证自动连接成功，未执行新增或保存接口。

以后数据库密码变更时，只需更新 Windows 用户环境变量 SIT_DB_PASSWORD。换电脑或换 Windows 用户时需重新配置。


## 场景 05：先试算，再提交借款（2026-10-16）

脚本：`tests/05_loan_application/test_start_loan.py`；公共流程：`common/loan_client.py`；参考产品配置：`config/loan_application.json`。

VS Code 入口：

- **05 · 借款申请 / 先试算后提交**：给 `.state/latest_user.json` 对应的最新成功新增用户申请借款。
- **06 · 完整借款流程 / 新增用户 → 资料 → 试算 → 提交**：依次运行新增用户、身份证及银行卡保存、试算和提交，任一步失败立即停止。
- **00 · 完整流程** 继续仅执行新增用户、身份证和银行卡，不包含借款。需要借款时明确选择 05 或 06。

终端：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/05_loan_application -v -s -x
```

产品参数按用户提供的参考请求保存：产品 001412、金额 2200、3 期、每期 7 天、总利息 1、服务费 1%、展期率 1%、罚息率 10%、skipUserConfirm=true、materialCompletionMode=true。channelType=2、productMode=2、chargingMethod=1 必须与实际表单一致。UUID、商户与当前默认收款卡 ID 从实际借款用户表单读取，绝不照抄参考用户 UUID 或银行卡 410。loanDate 留空，由后端按商户当地日期计算。

流程：

1. 登录并定位唯一目标用户，检查后台前置资格、可用产品和已有借款。
2. SQL 校验当前用户身份证、OCR 图片和所选收款银行卡的归属。
3. POST `/user/user/offline/startLoan/trial`，验证试算 code=0、期数、递增还款日、本金与利息合计、费用字段。
4. 确认试算没有生成借款主单，将本次试算的还款计划、税费、实到金额和服务费字段带入提交请求。试算之后改变参数或修改计划会被拒绝，必须重新试算。
5. POST `/user/user/offline/startLoan` 仅一次，再校验数据库主记录、各期本金/利息/费用/还款日及收款卡。

数据库：`off_mex_saas_loan.bor_pre_repayment_plan_header`、`bor_pre_repayment_plan`、`bor_pre_repayment_payout_card`。可通过 SIT_DB_LOAN_SCHEMA 覆盖库名。接口百分比按除以 100 与数据库利率比较，金额使用 Decimal 避免浮点误差。

重复提交保护：同一用户已有任何借款主记录时停止；发送提交请求前，将尝试写入 `.state/loan_attempts.json`。即使提交超时或后续校验失败，也不会自动再发一次。遇到该文件中的历史尝试，需要先核对后台及数据库，不要直接删除记录重跑。修改配置文件只影响下次新试算，可用 LOAN_CONFIG_FILE 指定其他配置的绝对路径。

本次实测：用户 739 / 手机号 0658900207，产品 001412、金额 2200、3 期，试算实到金额 2178、税费 0。试算成功后提交一次，code=0，接口提示“系统正在自动放款，请让客户稍后查账，部分场景下需要人工审核”。借款 header_id=`14ac9cea95784a1c9db64f6302078383`。主单、3 期还款计划、当前用户的收款银行卡数据库校验通过，**1 passed in 15.15s**。此结果证明申请提交和计划落库成功，不代表已经到账。

本次没有新增用户或再次保存身份证/银行卡，原有手机号、证件日期和银行卡序号未消耗。


## 场景 07：借款人审三种互斥结果（2026-10-16）

脚本 `tests/07_payout_approval/test_payout_approval.py`；公共方法 `common/approval_client.py`；原因及期望额度配置 `config/approval_actions.json`。

| VS Code 入口 | APPROVAL_ACTION | POST 接口 | 原因字段及默认值 | 审批数据库目标状态 |
| --- | --- | --- | --- | --- |
| 07.1 · 人审 / 通过（正向） | pass | /borrower/payoutApproval/pass | approvalReason=AutoCodeXtesting-pass | 1 |
| 07.2 · 人审 / 拒绝 | reject | /borrower/payoutApproval/reject | rejectReason=AutoCodeXtesting-reject | 2 |
| 07.3 · 人审 / 退回信审 | close | /borrower/payoutApproval/close | reason=AutoCodeXtesting | 3 |

三种场景均使用表单 POST，公共字段为 id 和 expectedAmount（默认 2200）。只运行明确选择的一个结果，没有默认审批动作。通过、拒绝、退回信审不能在同一张审批单上依次执行；测试互斥场景需要不同的待审批单。现有 00 和 06 完整流程不会自动执行人审。

默认目标来自最新新增用户的、已通过数据库校验的借款记录：读取 `.state/latest_user.json` 和 `.state/loan_attempts.json`，用 header_id 查询关联的唯一待审批记录。没有人审记录、已有审批结果、或存在多张待审单时立即停止，不猜测、不改选其他用户。参考请求里的三个 id 不写死。

需要明确操作其他审批单时，可设置 APPROVAL_ID；脚本仍核对发起借款类型、待审状态、关联主单和合同金额。APPROVAL_REASON 可覆盖原因，APPROVAL_EXPECTED_AMOUNT 可覆盖期望额度。当前场景要求期望额度与关联借款合同金额一致，遇到不同额度停止，先核对配置。

```powershell
# 第一正向：通过；第二：reject；第三：close。每次只选一种。
$env:APPROVAL_ACTION = 'pass'
.\.venv\Scripts\python.exe -m pytest tests/07_payout_approval -v -s -x
```

执行顺序：登录 → 确认审批账号启用 → 定位审批单 → SQL 校验待审状态 0、approval_type=1、主单归属及金额 → 核对详情 ID → 重查状态和 version → 记录提交尝试 → 只调用一个接口一次 → SQL 断言。

SQL 断言覆盖 off_saas_saas.bor_payout_approval 的目标状态、version 更新、审批原因、拒绝/退回原因、expected_amount、审批人和审批时间，以及 bor_payout_approval_log 的本次 PASS/REJECT/CLOSE 人工日志、from_status=0、to_status、操作人和原因。目标主单来自 off_mex_saas_loan.bor_pre_repayment_plan_header。数据库字段映射经现有历史数据只读验证。

提交尝试保存在 `.state/approval_attempts.json`，按审批 ID 记录选择结果及进度。网络超时、接口失败或数据库校验失败不自动重试、不自动改为其他结果；须先核对真实状态。不要删除记录来绕过保护。

本次验证：三个接口的页面请求协议确认；11 项新增本地测试通过；三种结果的历史数据库状态及日志映射只读验证通过。未提交任何新的通过、拒绝或退回审批，尚未对三种 POST 做新的写入实测。此前用户 739 的审批单 a28e2d4728be4d60ae50f2f68caae038 已处于拒绝状态 2，默认目标再次运行会被阻止；需要新的待审借款或明确指定一张未审批记录。

“通过”证明人审结果通过，不能直接等同于放款到账；“退回信审”校验本审批记录状态 3 和 CLOSE 日志，不将其冒充为下游信审人员已完成处理。


## 场景 08：放款审批成功（2026-10-16）

脚本 `tests/08_disbursement/test_payout_success.py`；公共方法 `common/payout_client.py`。接口 POST `/pay/borTransferBillMoco/edit` 使用表单编码，目标 state 固定为 1。

VS Code 新入口：

- **08 · 放款审批 / 放款成功**：给最新新增用户已借款且人审通过的唯一放款单改为成功。
- **09 · 正向全流程 / 新增 → 资料 → 借款 → 人审通过 → 放款成功**：依次执行 02、03、05、07（pass）、08，任一步失败停止。该入口明确包含人审通过，且清空继承的 APPROVAL_ID 以免错选其他用户。
- 00、06 保持原范围，不自动追加人审或放款审批。

请求包含 id、ids（必须同一单据）、systemSourceId=BORROWER、contractId、bizId、state=1、tradeTime、completeTime、arriTradeTime、updateReason。单据 ID 和时间全部从当前单据表单读取，不复用参考请求。仅支持状态 2（失败）、4（待处理）、6（处理中）改为成功，已成功或退票不重复修改。

关联链经实际数据库确认：借款主单 bor_pre_repayment_plan_header 的 VIRTUAL_ORDER_ID → 人审 apply_id → bor_contract.VIRTUAL_ORDER_ID（同时核对 SALE_UUID 和 OWNERSHIP）→ 放款单 BID_ID=真实 C13 合同号、CONTRACT_ID=borrower 申请号 BOR...，而不是把放款表字段 CONTRACT_ID 误当作真实合同号。放款金额校验为试算到手额，本例为 2178，并非合同额 2200。多卡、分批或多张放款单时停止，不随意选一张。

时间使用页面当前商户时间：执行时间保留原值，完成时间使用编辑页提供的值；到账时间按表单保留为空。校验时以 America/Mexico_City → Asia/Shanghai 转换后对比数据库，可通过 PAYOUT_MERCHANT_TIMEZONE、PAYOUT_DATABASE_TIMEZONE 覆盖。Windows 时区数据依赖 tzdata（已加入 requirements.txt）。不以电脑当前时区或参考请求的旧日期硬填。

数据库校验覆盖 off_mex_saas_payout.pay_trade_bill_moco 的状态 1、关联 ID、金额、执行/完成/到账时间、修改人及原因；pay_trade_bill_flow 中本次操作人对应的新成功流水；off_mex_saas_loan.bor_contract 合同状态 1（还款中）及放款时间；off_mex_saas_borrower.sal_apply_info 状态 11（完成放款）；bor_loans_main 的 LOANS_STATUS=1。可用 SIT_DB_PAYOUT_SCHEMA、SIT_DB_BORROWER_SCHEMA 指定库名。

每次修改前写入 `.state/payout_attempts.json`，请求失败、超时或 SQL 校验失败不会自动再发。允许读库等待异步同步最多 60 秒；不会重发业务请求或通过 SQL 修改数据。PAYOUT_UPDATE_REASON 可覆盖修改原因，默认沿用空白表单，与参考请求一致。

放款审批通过代表测试系统的放款记录与借款/合同状态同步成功，不等同于已核实真实银行到账。


### 本次全流程实测结果与独立审批账号

本次只新增一个用户：ID 861，手机号 0658900208；身份证 MAPA750106HMCRND05；银行 37006、卡号 1254112541000006。产品 001412、合同金额 2200、实到金额 2178，3 期、每期 7 天。借款主单 cd78b690afb84948872f18c520a1b0f3。

- 新增用户及数据库校验通过。
- 资料登录遇到验证码错误后，从资料步骤续跑，身份证和银行卡保存及数据库校验通过。
- 先试算，再提交借款，主单、3 期还款计划及收款卡校验通过。
- 原发起账号尝试人审时，接口明确拒绝“不能审批本人发起的放款申请”。在用户提供独立审批账号后，仅续跑人审和放款。人审单 f1485fdecd2547298148ef1f2eb60fff 已通过。
- 对应合同 C1320261016115631B7p7O，借款申请号 BOR202610161156313zoUwI，放款单 18505808fcec4408a1c7261e1185a659，放款修改接口 code=0、msg=修改成功；状态 1、修改流水、合同还款中、borrower 完成放款等校验通过。
- 最后续跑的“人审+放款”两个场景结果：2 passed in 22.77s。本地规则验证共 50 项通过。没有为了凑单次全绿而再新增第二个用户。

独立审批支持：APPROVER_USERNAME 与 APPROVER_PASSWORD 必须同时提供，仅人审场景使用；其他场景仍使用 SSO_USERNAME 与 SSO_PASSWORD。密码不保存到代码或状态文件。当前 07/09 默认使用 ob5-mx 审核 ob4-mx 发起的借款，禁止回退到发起人审批。两个审批账号变量已配置到 Windows 当前用户环境；公共方法支持直接读取，因此已启动的 VS Code 也能读取，无须每次输入密码。

```powershell
$env:APPROVER_USERNAME = 'ob5-mx'
$approverSecret = Read-Host '独立审批账号密码' -AsSecureString
$env:APPROVER_PASSWORD = [System.Net.NetworkCredential]::new('', $approverSecret).Password
```

借款提交记录会保存实际请求账号 submitted_by，供人工审批追踪；脚本在发送请求前阻止发起人审核自己的借款，服务端继续校验实际权限。审批表 creator_id/creator_name 可能表示归属员工，不能将其直接等同于实际 API 请求账号；服务端权限及同人审批规则始终是最终依据。对于没有历史记录的外部审批单，可用 LOAN_REQUESTER_USERNAME 明确提供实际发起账号后核对。

仅在服务端明确拒绝同人审批、数据库仍为原待审版本时，允许用户明确换另一审批账号继续相同结果；保留原尝试历史。网络超时等未知结果仍禁止自动重试。已通过、已拒绝、退回后的同一单不允许切换结果。


### 独立人审自动化规则（2026-10-16 更新）

默认分工：ob4-mx 新增用户、维护资料、申请借款及放款审批；ob5-mx 执行人审。07 的通过/拒绝/退回信审及 09 全流程均使用公共独立审批账号解析。包含人审的流程在首个业务步骤前检查账号不同且密码存在，防止新增用户和提交借款后才发现审批配置缺失。APPROVER_USERNAME/APPROVER_PASSWORD 仅保存于本机当前用户环境，项目和日志不保存密码；进程环境可覆盖本机设置。实际借款发起人以 submitted_by 为准，员工 WhatsApp 和审批表归属字段不替代该记录。审批动作仍按用户要求选择，未知请求结果不自动重试。
