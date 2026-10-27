> 2026-10-21 罚息行为修正：场景13新增逾期时，当前任务可在首次执行中计算多天罚息。请始终设置 OVERDUE_PENALTY_DAYS=1，不能把逾期天数填写成任务轮数；13/13.1 的 VS Code 配置已固定为1。本文更早关于“逾期几天就执行几轮”的描述已不适用。13.2仅在明确追加罚息时使用，金额未增长即停止。
# OFFSAAS 接口自动化测试



## 自动读取本机登录配置（无需手动输入密码）

当前电脑已配置 ob4-mx 登录账号及密码，保存在 Windows 当前用户环境，不保存在项目、VS Code运行配置、日志或Git仓库。common/sso_credentials.py 默认直接读取本机配置，因此已经打开的 VS Code 也可以使用，无须重启或手动设置 SSO_PASSWORD。终端以前误输的账号/密码不会覆盖本机默认配置。显式传入的独立审批账号仍使用 ob5-mx，不会被替换。

现在运行时跳过 Read-Host、$loginSecret、SSO_USERNAME/SSO_PASSWORD 手动输入步骤。使用09或09.1的F5入口，或者在项目终端设置对应借款配置、审批金额后，直接执行完整流程命令即可。09.1为6000元三期，09沿用默认2200元配置。数据库及独立审批密码继续自动读取。旧说明中要求每次手动输入登录密码的步骤已不再适用于这台电脑。

高级用法：只有明确设置 SSO_CREDENTIAL_SOURCE=process 时，才改用当前终端提供的完整 SSO_USERNAME/SSO_PASSWORD；日常使用保持默认local。所有VS Code入口明确设置local。修改账号时应同时更新本机账号和密码，禁止拼接不同来源的账号密码。自动读取凭据不保证验证码永远识别正确；验证码错误会单独报告，登录401不会再附加误导性的验证码提示。

## VS Code 终端启动与 Python 运行

本项目默认终端为 OFFSAAS - Windows PowerShell，使用系统 Windows PowerShell 5.1、-NoProfile 和独立的系统模块搜索路径，避免继承 Codex PowerShell 7 模块。Python 扩展自动激活已关闭，不需要执行 Activate.ps1，也不需要修改执行策略或在发布者提示中选择“始终运行”。不显示 (.venv) 前缀是正常的，直接指定项目解释器即可。

配置更新后请点击终端面板垃圾桶关闭原有错误终端，再点击“终端 → 新建终端”。若仍出现旧行为，先 Ctrl+Shift+P 运行 Developer: Reload Window（开发人员: 重新加载窗口），然后新建终端；不要继续复用旧终端进程。默认关闭本项目终端会话恢复，避免恢复已损坏的会话。

在 api-tests 目录执行以下命令检查环境（不会调用业务接口）：

```powershell
.\.venv\Scripts\python.exe -m pytest --version
```

运行具体场景时也使用 .\.venv\Scripts\python.exe -m pytest 加场景文件；环境变量仍使用 PowerShell 的 $env:NAME='value' 写法。F5 调试入口已明确指向项目 .venv Python，不依赖终端激活。业务认证信息仍需沿用对应场景的环境配置。

## 13 · 生成逾期订单与罚息（独立复用）

入口：`tests/13_overdue/test_generate_overdue.py`。公共逻辑：`common/overdue_client.py`、`common/overdue_fixture.py`、`common/overdue_database.py`、`common/scheduled_jobs.py`。任务与时区配置：`config/overdue.json`。

本场景用于明确指定的测试订单，要求先完成 11 生成预提醒和 12 预提醒分单，合同已成功放款、仍在还款中，目标为最早未还期次且未部分还款或减免。不存在预提醒、未分单或多笔匹配时停止，不自动挑单。默认只操作指定期次，其他期次保持原状，不使用仅按合同编号更新全部期次的 SQL。

VS Code 提供以下入口：

- **13 · 只读预检**：查看目标、服务端日期、真实账龄和任务配置，不修改数据库、不执行任务。
- **13.1 · 生成逾期订单及罚息**：输入预提醒订单 ID、逾期天数、本次唯一请求标识；罚息任务固定执行一次。
- **13.2 · 已逾期订单追加罚息**：要求已有逾期合同、催收记录及至少一轮罚息，只回拨罚息更新时间并追加指定轮数，不改应还日、不重新入催；未增长立即停止，任务轮数不等于逾期天数。
- **13.3 · 三期订单只读结果核验**：输入已有逾期请求标识、预期逾期天数、三期本金合计及当前预期罚息金额；仅查询已有结果，不修改日期、不执行任务。

也可按合同+期次运行，以下先预检：

```powershell
$env:OVERDUE_ORDER_ID = ''
$env:OVERDUE_CONTRACT_ID = '<实际合同编号>'
$env:OVERDUE_PERIOD = '1'
$env:OVERDUE_MODE = 'create'
$env:OVERDUE_DAYS = '1'
$env:OVERDUE_PENALTY_DAYS = '1'
$env:OVERDUE_DRY_RUN = '1'
.\.venv\Scripts\python.exe -m pytest tests/13_overdue/test_generate_overdue.py -v -s --tb=short -x
```

实际执行同一目标时，设 `OVERDUE_DRY_RUN=0` 并填写 `OVERDUE_REQUEST_ID`，例如本次独立操作标识 `overdue-order-period1-run01`。失败或超时后重跑必须沿用同一标识，不能删除记录或换标识绕过。新增模式的 OVERDUE_PENALTY_DAYS 固定为1，脚本默认值与VS Code一致；填写其他值会在日期写入前停止。逾期天数支持1～365天；只有明确的13.2追加模式才支持最多30轮任务，轮数不代表罚息覆盖天数。

完整顺序：

1. 只读核验订单、预提醒已分单、成功放款及未还状态；读取服务端商户时钟、真实账龄配置和两项任务。
2. 计算“服务端业务今天减逾期天数”的 01:00，转换到数据库存储时区。锁定合同和两条目标记录，在同一事务中仅修改 REPAY_TIME、REPAYMENT_DATE。行数或快照不符时回滚，避免一张表成功另一张表失败。
3. 执行一次入催任务 `ea6ce1c5829811f18e13fa163e9c0712 / loanTask.executeCreateRemTimer`，参数为空；等待新成功日志，再校验合同状态 1→2、新催收单的合同/期次/逾期天数及实际账龄。
4. 执行一次罚息任务 `ea6ce6cb829811f18e13fa163e9c0712 / loanTask.executeNewPenaltyTimer`，参数保持 `200`；等待新成功日志，验证 LATE_CHARGE_ORIGIN 增长、罚息时钟推进、本金及应还日不变，并回查合同还款计划接口。
5. 新增逾期到此完成，不按逾期天数重复执行罚息任务。只有明确选择13.2追加模式时，才在已有罚息的前提下回拨 PENALTY_UPDATE_TIME 并逐轮核验；金额未增长立即停止。

时间使用 `/system/job/edit/{入催任务ID}` 页面服务端下发的商户当前时间，并与 HTTP Date 核对。当前 MX 业务时区 America/Mexico_City，数据库存储时区 Asia/Shanghai，写入前通过数据库 NOW()/UTC_TIMESTAMP() 核验。这里的 Asia/Shanghai 仅用于存储时区转换，不用于确定业务“今天/昨天”。例如服务端业务时间为 2026-10-21，业务昨天 2026-10-20 01:00-06:00 对应数据库 DATETIME 2026-10-20 15:00。不会把电脑日期或未经时区转换的参考 SQL 日期直接写入。执行中跨日或任务参数变化则停止。

**账龄以实际环境配置为准。** 当前 MX 配置是 1天=S0、2–3天=S1+、4–60天=S2+，与通用描述“1–3天=S1、4–6天=S2”不一致。脚本同时展示描述性分组和实际名称/编码，入库校验使用实际编码。需要严格匹配名称时设置 `OVERDUE_EXPECTED_GRADE`，例如设置 S1 而实际为 S0 时会在任何写入前停止。脚本不修改账龄规则。

罚息校验默认确认每轮金额增加、时钟推进、后续每轮与首轮观察到的增量一致，以及接口与数据库金额一致。可设 `OVERDUE_EXPECTED_DAILY_PENALTY` 精确核验单次任务的金额增量（变量名保留兼容历史配置，不代表它一定是单日罚息）；未提供时，不将增长检查宣称为已证明业务计息公式或正确封顶。达到计息上限、无增长或金额不一致会停止，不通过 SQL 修改罚息金额“修正”结果。`200` 是任务的现有参数，不作为罚息金额或天数解释。

数据库写入能力仅位于 `common/overdue_fixture.py`，只允许 sit-db01.suxi-test.com:3306 / off_mex_saas_loan 的限定日期字段：场景13为REPAY_TIME/REPAYMENT_DATE/PENALTY_UPDATE_TIME，场景14额外允许rem_bill.OVERDUE_DATE，按 ID+合同+期次+原值限定；普通 `Database.select()` 及其他场景仍只读。入催和罚息任务是当前国家共享任务，每次执行也可能处理其他符合条件的订单，追加多轮也会影响其处理次数。任务本身可能产生其他业务副作用，脚本不通过直接改合同状态或催收表伪造结果，也不在任务执行后自动恢复日期。

进度、原始计划快照、任务日志 ID、每轮罚息增量记录在 `.state/overdue_attempts.json`。SQL 提交结果或任务结果未知时禁止自动重发；要先只读核对现场后再确定可继续的阶段。已有成功请求只回查原结果。已存在罚息的订单必须显式选 `penalty_only`，不能重新执行新增逾期来清空旧过程。追加模式下 OVERDUE_PENALTY_DAYS 表示本次新增轮数，OVERDUE_DAYS 留空时按服务端日期和原应还日读取实际天数。

早期预检记录（后续真实造数结果见本文实测章节）：当时仅完成代码、本地事务/边界测试和真实环境只读预检：使用已有订单核实前置条件、服务端时钟、任务参数和实际账龄，并比较预检前后两表日期、罚息金额、合同状态、两个任务日志数量均未变化。没有将任何订单改为逾期，也没有执行入催或罚息任务。真实造数需用户指定目标订单后再执行；日志为 artifacts/overdue_preview_941_20261021.log。

## 12 · 预提醒手工分单：精准转交给 ob4-mx

独立脚本：`tests/12_prealert_assignment/test_assign_prealert.py`。公共请求方法：`common/prealert_assignment.py`；数据库校验：`common/prealert_database.py`；固定配置：`config/prealert_assignment.json`。不创建用户，不申请借款，不改还款日期，不执行定时任务，只处理已存在的有效未还预提醒。

VS Code 的“12 · 预提醒分单”输入单笔预提醒订单 ID 即可运行；“12.1”可输入手机号，前提是能唯一定位有效合同和预提醒。多笔时必须提供准确订单 ID，或合同编号与期次，不自动选最新一笔。

```powershell
# SSO_USERNAME / SSO_PASSWORD 按公共登录配置提供。
$env:PREALERT_ORDER_ID = '813f8a163c504a60b84c4c4670265aa5'
$env:PREALERT_ASSIGN_PHONE = ''
$env:PREALERT_ASSIGN_CONTRACT_ID = ''
$env:PREALERT_ASSIGN_PERIOD = ''
.\.venv\Scripts\python.exe -m pytest tests/12_prealert_assignment/test_assign_prealert.py -v -s --tb=short -x
```

也可清空 PREALERT_ORDER_ID，用 PREALERT_ASSIGN_CONTRACT_ID 和 PREALERT_ASSIGN_PERIOD 定位；或仅设置 PREALERT_ASSIGN_PHONE。手机号存在多个应用时使用 PREALERT_ASSIGN_APP_OWNERSHIP，并通过合同参数消除多合同歧义。提供多项标识时必须相互一致。

接收部门固定为 11807 / 墨西哥二部-预提醒，接收人固定为 1590 / ob4-mx。提交前从数据库检查用户、部门归属及启用状态，并调用 `/loan/remindBill/selectUsersByDeptList` 确认此人确实是可选接收人。若用户或部门变更则停止，不静默选择其他人。

表单协议来源于 `/loan/preReminder/distributeOrTransferBills`。提交 `/loan/preReminder/updateDistributeOrTransferBills`，使用单笔 `ids`、`distributionMode=1`（精准分配）、`opType=2`（工单转交）、`deptIdList=11807`、`ownerDictList=1590`、`userName=1590-ob4-mx`、`isAssistAllocation=1`（否）。不会使用顺序分配或按空 IDs 筛选批量分单。提交前再次比较原记录，检测并发归属变化。

发送前写入 `.state/prealert_assignment_attempts.json`，超时或结果未确认时禁止自动重发。已归属 ob4-mx 的订单只做数据库回查，不生成重复流水。数据库检查 pre_pend_repayment 接收人/部门、分单标志及时间、原日期金额不变，并确认新增唯一 pre_status_detail 的接收人/部门/操作人/原归属/工单转交动作。API 列表是否可见独立记录，不能用接口 code=0 代替归属及流水校验。

本次实测（2026-10-21）：另选当天已完善资料的用户 941 / 0658900214，试算提交借款 2200，由 ob5-mx 人审通过后放款成功，合同 C1320261021154445LQ2NR。生成第 1 期 T-2 预提醒 813f8a163c504a60b84c4c4670265aa5，应还日期 2026-10-23、待还金额 733.66；随后用 ob4-mx 精准转交至本人及部门 11807，接口及归属、流水数据库验证成功，分单场景 1 passed in 16.02s。只提供订单 ID 再运行，1 passed in 7.42s，未重复提交。110 项本地规则测试通过。用户已手工转交的 942 订单未再次提交转交。分单日志：artifacts/assign_prealert_941_20261021.log；准备和生成日志分别位于 artifacts/assign_prepare_941_20261021.log、artifacts/assign_generate_941_20261021.log。当前账号列表仍未返回目标记录，api_list_visible=false，归属成功由数据库和新增流水确认。

本次准备链路使用 LOAN_TARGET_PHONE=0658900214，使场景 05/07/08 始终处理同一现有用户，保留 latest_user.json 原内容。该变量仅用于指定现有用户；00/06/09 新增用户流程会清空并预检该变量，避免错配用户。

## 11 · 预提醒生成：按手机号独立复用

脚本：`tests/11_prealert/test_generate_prealert.py`；接口公共方法：`common/prealert_client.py`；数据库断言：`common/prealert_database.py`；任务配置：`config/prealert.json`。

VS Code 已增加 11（跟随任务参数）、11.0（T0）、11.1（T-1）、11.2（T-2）四个入口。按 F5 后输入已放款用户手机号即可运行，不需要该用户出现在 latest_user 状态中。只有一笔有效合同且没有历史预提醒的最早未还期次会自动选中；多笔有效合同时，须明确设置 PREALERT_CONTRACT_ID，不能任意选择。该场景只处理现有已放款合同，不新增用户或申请借款。

```powershell
$env:PREALERT_PHONE = '0658900215'
$env:PREALERT_DAYS = '' # 空值跟随任务当前 params；0=T0，1=T-1，2=T-2
# 多笔有效合同时才指定合同；可用 PREALERT_APP_OWNERSHIP 消除手机号跨应用歧义。
$env:PREALERT_CONTRACT_ID = ''
.\.venv\Scripts\python.exe -m pytest tests/11_prealert/test_generate_prealert.py -v -s --tb=short -x
```

执行顺序：确认用户、放款单成功及合同还款中 → 查询 `/system/job/list` 核验预提醒任务 → 读取服务端下发的商户当前时间并与 HTTP Date 校验 → 按真实任务参数计算日期 → 通过 `/loan/contract/saveRepaymentPlan` 保存目标期次日期 → API 和数据库回查 → 再检查任务参数与服务端日期未变化 → `/system/job/run` 执行一次 → 等待任务成功日志和新预提醒记录 → `/loan/preReminder/list` 回查列表可见性。

默认不修改任务参数；显式指定账龄与当前参数不同时，先调用 `/system/job/edit` 保存参数并核验，保持 cron、数据组、国家范围及启停状态不变，再计算并保存还款日期。任务是当前国家的共享任务，会同时处理其他符合条件订单，修改的参数会保留，不自动改回旧值。已生成数据不能仅凭 run 返回成功判断，必须核验 schedule_job_log 和 pre_pend_repayment。

时间取自服务端页面的商户国家时间，不使用本机时间，也不直接把数据库墙钟当作墨西哥日期。当前 MX 对应 America/Mexico_City；时区不匹配、跨日、任务参数被并发修改时停止。MX 的 0/1 天账龄显示名称可能是 D0/D-1，脚本按天数与国家读取真实账龄编码，不硬编码名称。

只修改最早未还期次的应还日；还款计划原有金额、状态及其他期次的日历日期均校验不变。接口会重新归一化各期日期的时分秒，并将 MITIGATE_LATE_CHARGE / AMOUNT_DUE_BEFORE_SETTLEMENT 的空值保存为 0，校验只针对这两个已观察字段允许空转零，不能掩盖本金等金额变化。当前不支持部分还款、已逾期、已生成其他账龄数据或存在未知操作结果的期次。

操作尝试在发送请求前保存到 `.state/prealert_attempts.json`。保存或执行的未知结果不自动重试；同一合同/期次/日期/账龄已成功时仅回查，不重复改期或执行任务。若接口已明确保存成功但后续校验失败，应先只读核对原始快照与数据库，再从未发送的后续步骤继续，不得删除尝试记录重跑。

本次实测（2026-10-21）：选取用户 942 / 0658900215，先试算并申请 2200、3 期、每期 7 天，人审由 ob5-mx 通过后，合同 C1320261021150342BSR8l 放款成功。任务 params=2，服务端业务日期 2026-10-21，将第 1 期应还日改为 2026-10-23，生成 T-2 预提醒 85fefc1eea264e4ab76fc26a0836e8c9，待还金额 733.66，任务成功日志 c5d2aa31-51ca-4637-9219-79dced6dbb2f。金额、日期、账龄及任务日志数据库校验通过。当前 ob4-mx 的列表接口未返回该记录，因此结果单独标记 api_list_visible=false，不将数据库成功混同为列表可见；列表查看还需核对该账号的数据可见范围。其余 4 位批次用户未借款。日志位于 artifacts/prealert_prepare_942_20261021.log 和 artifacts/prealert_942_20261021.log。

验证结果：95 项本地规则测试通过；针对同一手机号和已成功记录再次运行场景 11，1 passed in 10.36s，仅回查，未重复改期、未重复触发任务。T0/T-1 的日期计算及修改任务参数的表单协议通过本地验证，本次真实业务生成只执行 T-2。

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


## 场景 14：已有逾期订单更新为逾期 X 天

入口：`tests/14_overdue_days/test_update_overdue_days.py`，公共可复用方法：`common/overdue_days.py::update_overdue_days`。

VS Code 已增加 **14 · 更新逾期天数 / 只读预检**、**14.1 · 更新逾期天数 / 指定订单 → 逾期 X 天**。输入催收单 ID（不是预提醒 ID）、目标逾期天数，以及实际执行时的唯一请求标识。没有默认真实订单。支持 1～365 天；仅处理已有唯一、未结清、单期催收单、最早未还期次，要求之前已完成预提醒和分单。多期合并催收、其他期次已到期、部分还款或减免时停止。

采用正常流程：读取服务器商户业务时间（当前 MX 为 America/Mexico_City），应还日=业务今天-X天，入催日=应还日+1天。两者以业务时间 01:00 计算并转换成数据库时区后，在同一事务中精确修改还款计划 REPAY_TIME、预提醒 REPAYMENT_DATE、催收单 OVERDUE_DATE。每行用 ID+合同+期次+原值限定，行锁核对完整快照，任意一步失败则回滚。不直接修改 OVERDUE_DAYS、账龄、金额或合同状态。

随后 POST `/system/job/run`，表单只有本次读取的任务 id。任务固定为 `ea6ce20a829811f18e13fa163e9c0712` / loanTask.updateRemindOverdueDays，先通过 `/system/job/list` 及数据库确认 params=0、dbGroupId=1、targetGroup=2；不自动更改任务参数。参数 0 表示所有账龄，这是当前国家的共享任务，可能更新其他符合条件的催收单。

校验覆盖三表日期、目标 OVERDUE_DAYS、唯一新成功任务日志及实际参数、原有本金/罚息/其他金额、分单归属与其他期次不变、合同仍为逾期，以及还款计划 API 与数据库一致。催收列表返回目标时额外核对天数；无列表权限时记录 api_list_visible=false，数据库生成/更新成功与列表可见分别报告。账龄码记录真实结果，不假设此任务同时完成账龄迁移。本场景不执行罚息任务，更新为 X 天不代表补齐 X 天罚息；罚息仍通过 13.2 单独操作。

PowerShell 示例（SSO 与数据库认证沿用公共配置）：

```powershell
$env:UPDATE_OVERDUE_COLLECTION_ID = '你的催收单ID'
$env:UPDATE_OVERDUE_CONTRACT_ID = ''
$env:UPDATE_OVERDUE_PERIOD = ''
$env:UPDATE_OVERDUE_DAYS = '2'
$env:UPDATE_OVERDUE_DRY_RUN = '1'  # 先只读预检；实际执行改为 0
$env:UPDATE_OVERDUE_REQUEST_ID = '订单标识-days2-本次唯一标识'
.\.venv\Scripts\python.exe -m pytest tests/14_overdue_days -v -s -x
```

也可以清空 UPDATE_OVERDUE_COLLECTION_ID，提供 UPDATE_OVERDUE_CONTRACT_ID 和 UPDATE_OVERDUE_PERIOD 精确定位。只读模式不写库、不执行任务、不生成操作状态。真实执行的原始快照和逐步状态保存于 `.state/overdue_days_attempts.json`；重跑同一标识只读回查，结果未知时阻止自动重发，也不能换标识绕过。与场景 13 共用订单锁并相互检查未确认记录。

2026-10-21 验证：147 项本地测试通过，覆盖跨年/闰年日期、三表原子提交、第三表失败回滚、无效订单拦截、未知结果禁止重发、成功请求只读回查。用户 941 的已有催收单仅执行了“目标 2 天”的真实环境只读预检，1 passed；任务协议、参数、库表和服务器时间通过核验，实际仍保持逾期 1 天。场景 14 的实际日期写入和任务 POST 尚未执行，不能把预检通过表述为已生成逾期 2 天。


2026-10-21 场景 14 实测：按用户指示将用户 941（0658900214）合同 C1320261021154445LQ2NR 第 1 期改为逾期 10 天。服务器业务时间 2026-10-21，业务应还时间 2026-10-11 01:00、入催时间 2026-10-12 01:00；存储时间分别为当日 15:00。三表日期事务、新任务成功日志、OVERDUE_DAYS=10、金额和其他期次不变及还款计划 API/数据库一致校验通过，1 passed in 15.88s。未执行罚息任务，原有罚息 220.00 保持不变。实际账龄码为 732181；催收列表接口未返回该单，api_list_visible=false。原始快照和成功状态保存在 .state/overdue_days_attempts.json，唯一标识 user941-period1-days10-20261021-01。


## 场景 15：催收手工分单，单笔转交 ob4-mx

脚本：`tests/15_collection_assignment/test_assign_collection.py`。公共复用方法：`common/collection_assignment.py::assign_collection`；数据库断言：`common/collection_assignment_database.py`（由公共 DatabaseChecks 集成）；固定配置：`config/collection_assignment.json`。

VS Code 已增加 **15 · 催收手工分单 / 只读预检**、**15.1 · 催收手工分单 / 单笔转交 ob4-mx**。输入催收记录 ID 即可，默认无真实目标；也可使用环境变量 COLLECTION_CONTRACT_ID + COLLECTION_PERIOD 唯一定位。必须已生成逾期催收单，合同状态为逾期、天数大于0，订单未结清或委外。此场景独立执行，不会自动新增用户或提交借款。自动分单没有纳入本场景，手工分配固定为精准分配 distributionMode=1、转交 opType=2。

固定接收部门为 11807（墨西哥二部-预提醒），接收人 1590 / ob4-mx。先核验启用状态和部门可选接收人，再读取真实转交页面。页面 GET `/loan/remindBill/distributeOrTransferBills` 的 ids 需要 REM业务单号，提交 POST `/loan/remindBill/updateDistributeOrTransferBills` 的字段 id 则是记录UUID。脚本从同一数据库行读取并核验两者，不使用示例ID。提交表单含 deptSelect、deptIdList、ownerDictList、userName、opType、distributionMode、id 等，遵循真实催收协议；不复用预提醒的 ids 或 isAssistAllocation 字段。

```powershell
$env:COLLECTION_ORDER_ID = '你的催收记录ID'
$env:COLLECTION_CONTRACT_ID = ''
$env:COLLECTION_PERIOD = ''
$env:COLLECTION_DRY_RUN = '1' # 只读预检；实际转交改为 0
.\.venv\Scripts\python.exe -m pytest tests/15_collection_assignment -v -s -x
```

按合同定位时清空 COLLECTION_ORDER_ID，设置 COLLECTION_CONTRACT_ID 和 COLLECTION_PERIOD；存在多笔结果会停止。表单只允许包含一笔，提交前再次核对完整记录未变化。实际提交前将原始订单、还款计划和旧流水ID保存到 `.state/collection_assignment_attempts.json`。网络超时、未知结果或数据库断言失败均不自动重发；成功后已属于固定接收人则只读回查，其他人后续改派时不自动覆盖。

数据库校验：rem_bill.USER_ID=1590、OWNER_NAME=ob4-mx、DEPT_ID=11807、REMIND_STATUS=1；remind_status_detail 必须出现唯一新的转交流水，操作人、接收人、部门、状态、工单转交动作、入催日期、账龄、商户及原归属正确。分单前后应还日期、逾期天数、账龄、催收金额和全部还款计划（含罚息）不变。列表可见与数据库已转交分开记录。

2026-10-21 实测使用今日已有资料完整的用户 **938 / 0658900211**，没有新增用户，也没有重新分配用户941：

- ob4-mx 先试算、再提交借款，产品001412、金额2200、3期，每期7天，实到2178；主单 dea4f379aed24d0691824873e2bd97d5。
- ob5-mx 人审通过，审批单 ccaf47d0cbf745c282e47d5275742525。首次登录验证码错误，未提交审批；仅续跑人审和放款，没有重复申请借款。
- 放款单 10e650c6a01b4915bad408a1ac7d4d53 成功，合同 **C132026102116484951V3h**。
- 生成T-2预提醒 a50922c17e4741d7a742e72371f4fd7d，随后转交ob4-mx。
- 按服务器业务时间生成第一期逾期1天，实际账龄S0；罚息任务一次，罚息220.00。
- 催收记录 **0d43f96312ab40d8948d49cdb1ef05d3**，业务单号 REM20261021ulSq5xfu，手工转交返回 code=0、分单成功1、失败0。数据库归属及新流水 f86326df-e268-43d0-b5fd-041596c719b5 校验通过，分单测试1 passed in 9.64s。分单前后日期、天数、账龄、金额及罚息不变。当前账号列表接口未返回目标，api_list_visible=false，不能宣称列表已可见。

全流程各业务步骤均通过数据库校验，分阶段执行日志汇总在 `artifacts/collection_flow_938_full_20261021.log`，包含首次验证码失败及续跑记录；原始状态按各场景持久化，没有更改 latest_user 或递增序列。

场景15新增10项本地规则测试通过；项目全部本地规则测试158 passed in 8.69s。覆盖业务单号与记录ID区分、空/多笔表单、原归属变化、错误接收人、超时禁止重发、已归属仅回查及持久化未知结果拦截；VS Code配置与Python语法检查通过。


## 6000元三期、第一期逾期15天实测（2026-10-21）

已新增用户948，手机号0658900216，证件日期段750114，银行卡尾号0014。配置独立保存在 config/loan_6000_3periods.json，原2200配置未修改。新增VS Code入口09.1用于6000元三期的新增、资料、借款、人审通过及放款成功；此入口不自动执行预提醒/逾期步骤。

产品001412，合同本金6000，分3期，每期7天，试算到手5940。ob4-mx申请，ob5-mx人审通过，合同 C1320261021170627gJlli；新增/资料/借款/审批/放款5项均通过。预提醒 e6a3887818f849058fea9b5de1d48780，生成和分单分别通过。

按服务器Mexico业务日期2026-10-21，将第一期应还时间设为2026-10-06 01:00（数据库存储2026-10-06 15:00）。入催成功，催收记录2e31ee6e0c5c4a6a9353d7f16c86afb1，OVERDUE_DAYS=15，实际账龄S2+/747126。第二/三期未调整，仍为2026-11-04、2026-11-11。

首次罚息任务已产生9000.00。原脚本按15轮计划执行，第二次任务接口返回成功后发生本地原子替换PermissionError，因此原生成测试为FAILED，不能宣称原流程全绿。只读核对唯一新任务成功日志4935c510-5d47-4451-bfed-f6481e58290e，确认第二次执行后罚息仍9000.00，未重复发送任务，取消剩余13轮。原始计划、实际1次增长/2次任务、故障状态与核销证据保留在同一 .state/overdue_attempts.json 请求记录中。随后 tests/13_overdue/test_verify_existing_overdue.py 只读回查，三期本金合计、逾期15天、S2+、两表日期、罚息9000、原本金字段、API与数据库及成功任务日志均通过，1 passed in 7.13s，状态核销为db_verified。恢复后未核查催收列表可见性。

已修复新增逾期将天数当作任务轮数的问题，并对Windows本地状态文件临时占用增加有限重试，仅重试本地原子替换，不重试业务请求。用户948的最终状态已完成，本次没有执行催收分单。

2026-10-21免输入登录验证：清空进程SSO_USERNAME/SSO_PASSWORD后，登录接口及数据库校验1 passed；模拟终端遗留错误账号密码，仍使用本机ob4-mx完成后台OAuth登录，审批账号仍为ob5-mx。未新增用户或发起借款。新增8项凭据来源测试，全部本地测试170 passed in 9.87s。


## 09.2 / 09.3：新增并止步人审通过，不放款

- **09.2 · 人审通过止步 / 新增 → 资料 → 借款 → 人审通过（不放款）**：新增一位用户，保存身份证及银行卡，试算并提交6000元三期借款，ob5-mx审批通过，随后仅执行16只读未放款校验。每期7天，其他参数沿用 config/loan_6000_3periods.json。
- **09.3 · 批量新增 / 资料 → 借款 → 人审通过（不放款）**：在VS Code输入新增数量（1～20）及本批次唯一标识，逐人执行同一链路，每位均核验人审通过且未放款。默认数量5，没有默认批次ID。

两个入口均不包含08放款脚本，不调用放款修改接口；并非仅跳过08就算成功，最后需要确认审批状态1、独立审批人、放款单状态2/4/6、合同无放款日期且非还款中/逾期、没有成功放款流水。放款单状态2表示系统生成的失败状态，不冒称为待处理状态；如已经自动放款，校验会失败并停止，不通过SQL撤销或更改真实结果。

终端批量运行示例：

```powershell
.\.venv\Scripts\python.exe tools/run_approved_unpaid_batch.py --count 5 --batch-id your_unique_batch_id --loan-config config/loan_6000_3periods.json
```

账号、数据库及独立审批凭据自动读取本机配置。批次状态保存在 .state/<batch-id>.json，分用户/步骤日志保存在 artifacts/ 下；同批次ID再次执行会在新增前拒绝，避免误建另一批用户。失败时先核查批次当前步骤及各场景持久化状态，不要换批次标识直接重跑。只有明确在登录阶段失败的验证码9999会重新获取验证码（最多3次登录尝试），未知业务结果不会重发。

16核验脚本为 tests/16_approved_unpaid/test_approved_unpaid.py，单独运行时只读检查当前明确目标（LOAN_TARGET_PHONE指定或latest_user）已关联且提交成功的唯一借款，并保存 .state/last_approved_unpaid_result.json，不新增用户、不审批、不放款。

2026-10-22 实测批次 approved_unpaid_20261022_01：新增953/0658900218、954/0658900219、955/0658900220、956/0658900221、957/0658900222，均为6000元、3期、每期7天，资料/试算提交/独立人审通过及未放款核验各5项通过。第五名首次新增前验证码9999失败，重新登录成功后才新增，没有重复业务提交。全批次结束后统一只读复核5笔，审批均为1且由ob5-mx审核，放款单均为2，合同无放款日期，无成功放款流水；未放款阶段在bor_pre_repayment_plan核验3期本金合计6000。未执行08或放款修改接口。批次、分步骤和汇总日志均已保存；本地规则测试178 passed in 10.00s，VS Code入口和路径校验通过。


2026-10-22 用户956/0658900221复借：按明确手机号及用户ID定位，复借标识user956-repeat-20261022-01，保留原借款历史。6000元、3期、每期7天，先试算后提交一次，新主单7e4d727dab814e9597b592afabdd0143，提交场景1 passed in 11.87s。按用户补充要求仅继续ob5-mx人审通过，审批单b8459df12ec84342ba7004fa6d83cbf3，合同C1320261022135011j2j1g；人审+未放款只读核验2 passed in 14.93s，放款单76f1d1a99e6f48459227fd73085b7ac3状态2、合同无放款日期且无成功放款流水。未新增用户、未重复提交、未执行08。日志为artifacts/repeat_user956_0658900221_20261022_01.log和artifacts/repeat_user956_approval_unpaid_20261022_01.log。

VS Code的10入口现要求显式手机号、用户ID和唯一复借请求标识，移除旧用户默认值；10.1新增6000元复借→人审通过（不放款）入口，只含05、07和16。已成功复借请求不能用相同标识再次提交，未知结果不能换标识绕过，原历史不删除。

2026-10-22 用户957/0658900222复借：唯一标识user957-repeat-20261022-01，6000元、3期、每期7天，试算到手5940，提交一次并保留原借款历史。新借款主单996b1840eab74ea1b7d03a9c5545b602，人审单7763a8d40791496d882cae6b899507db由ob5-mx审核通过，合同C132026102214433833Mve。明确止步人审通过，放款单b12f570f27494111bdc5bef3752d8338状态2，合同无放款日期且无成功放款流水。借款、人审和未放款核验3 passed in 32.46s；未新增用户、未执行放款修改接口。日志artifacts/repeat_user957_approval_unpaid_20261022_01.log，实际进度及历史保存在对应.state文件中。

2026-10-22 批次 approved_unpaid_20261022_10users_02：实际新增10名用户963～972/0658900223～0658900232，均6000元、3期、每期7天，资料/试算提交/ob5-mx人审通过。逐笔16校验时均未放款，但最终统一复核发现963/0658900223后来变为放款成功，流水b0345cdc-0ac7-40b6-a983-bb62b2878e8f记录操作账号ob5-mx，合同放款时间2026-10-22 15:00:55（数据库存储值）。其余9笔仍为未放款。不能将本批次宣称为10笔当前均未放款；本脚本仅02/03/05/07/16，不包含08或放款修改接口，未回改已放款数据，未擅自补建第11名用户。第7名借款前验证码9999，仅重新登录。最终批次状态completed_with_later_payout_change，final_unpaid_count=9/final_paid_count=1；完整日志、原逐笔校验记录和最终审计保存在.state/approved_unpaid_20261022_10users_02.json及artifacts/approved_unpaid_20261022_10users_02_summary.txt。

2026-10-22 按用户要求只新增1名并发起借款：用户974/0658900233，6000元、3期、每期7天，身份证与银行卡保存、先试算再提交均通过，3 passed in 59.78s。主单8c97a2f4d3d148e2b49cfbfff8031500；只读核实审批单b09e50bd433b49719856dd3ca244fa22状态0、审核时间为空、无人工PASS/REJECT/CLOSE日志。未执行07人审或08放款。VS Code新增06.1入口，固定6000元三期，只含02/03/05，适用于新增到借款提交为止。日志artifacts/new_loan_no_review_20261022_01.log。

2026-10-22 按UUID f2e865c0c8c440cb928e08c3369bfdd4定位用户965/0658900225，核验本地借款历史及数据库归属一致后复借。唯一标识user965-repeat-20261022-01，6000元/3期/每期7天，试算后提交一次，新主单c34d7ecd96ef4c1889e1212382c63e21，1 passed in 9.46s，保留1笔原借款历史。只读核验新审批单c52b42d5f0324fa6aa7d35e3dc929264状态0、审核时间为空、无人工PASS/REJECT/CLOSE日志，无已放款合同或成功放款记录；未操作人审、未操作放款，未新增用户。日志artifacts/repeat_user965_no_review_20261022_01.log。

2026-10-22 批次approved_unpaid_20261022_5users_03：实际新增5名981/0658900234、982/0658900235、983/0658900236、984/0658900237、985/0658900238，均6000元、3期、每期7天，资料、试算提交、ob5-mx人审通过及未放款逐笔校验通过。最终统一复核：4笔未放款，1笔已放款；无额外新增，未执行08或放款修改接口。状态及最终快照保存在.state/approved_unpaid_20261022_5users_03.json，汇总及完整日志保存在artifacts/对应批次文件。

2026-10-23 批次approved_unpaid_20261023_10users_01：新增10名996～1005，手机号0658900239～0658900248，均6000元、3期、每期7天。资料/先试算再提交/ob5-mx人审通过/未放款逐笔校验均通过，最终统一复核9笔未放款、1笔已放款。第2、5、7名借款前及第9名人审前验证码9999，严格只在登录完成前重新获取验证码，未重复业务提交。未执行08或放款修改接口，无额外新增用户。状态及最终快照在.state/approved_unpaid_20261023_10users_01.json；完整、分步骤及汇总日志在artifacts/对应批次文件。


## 两位已放款用户复贷与按顺序逾期：2026-10-26 执行记录

本次计划 two_users_reloans_ordered_overdue_20261026_01 使用996/0658900239和997/0658900240；每人新增两笔6000元、3期、每期7天的申请，目标为每人三笔合同按创建先后将首期设为逾期3、2、1天。

实际状态：每人的第一次复贷已由ob5-mx人审通过并放款；第二次复贷返回code=0且主单、三期预还款计划已落库，但VIRTUAL_ORDER_ID为空，没有对应人审单或合同。待处理主单分别为c7c67be10247401cb7e2d1d1a053d8af和fd7b961e284c48369024d44047f27a38。最终只读核验每人3笔申请、2笔已放款合同；未修改应还日期，未执行预提醒或逾期任务，不能宣称逾期完成。

编排脚本tools/run_reloans_ordered_overdue.py已保存各阶段进度；审批解析增加最多60秒只读等待，不会重发借款。计划状态为awaiting_backend_virtual_order_generation，普通重跑会拒绝。应先排查两笔原申请为何没有生成虚拟订单，再核验关联记录并从未完成的人审阶段继续；禁止换申请标识、删除状态或重新提交这两笔借款。计划与审计分别保存在.state/同名JSON及artifacts/同名_audit.json，日志保留在artifacts/。


2026-10-26 后续执行：按用户要求直接处理现有已放款合同，未再提交借款、人审或放款。996和997各两笔合同首期分别逾期3、2天；原合同首期应还2026-10-23、第一次复贷首期应还2026-10-24（墨西哥服务器日期），实际账龄S1+，罚息分别1800和1200。四笔均完成预提醒、转交ob4-mx、两表限定日期修改、入催、一次罚息任务及接口/数据库校验；其他期次日期和本金保持不变。两笔第三次申请仍无虚拟订单/合同，逾期1天目标未执行。最终审计artifacts/two_users_reloans_ordered_overdue_20261026_01_overdue_audit.json。本地检查178 passed。


## 泰国线下大额：独立完整流程

泰国脚本保存在thailand/，操作说明见[泰国说明](thailand/README.md)。VS Code提供TH00/01/02/03/05/07/08/09入口，其中TH09为新增→身份证OCR→KBANK→借款→chengzi人审通过→放款成功。手机号、证件、银行卡分别递增，独立本机凭据及.state/thailand/，业务库固定泰国，公共数据库连接只读。

2026-10-27已实跑成功：仅新增172555/0650100002（起始号码1已占用），身份证1551500010011、KBANK卡102519931001，线下大额产品3500/3期/7天/总利息100，chengzi人审通过，合同C1320261027161023X3S6e，实际放款3465。接口、数据库及操作流水校验通过；200项本地检查通过。详情见artifacts/th_full_20261027_01_final_audit.json。下一手机号0650100003、证件1551500010012、银行卡102519931002。

泰国后续能力已补齐：TH10复借、TH11预提醒、TH12预提醒转交OB5、TH13逾期及罚息、TH13.2追加罚息、TH14更新逾期天数、TH15催收转交OB5、TH16人审通过未放款校验、TH17已放款合同到逾期串联（默认预检）、TH18新增到逾期完整流程；另支持人审拒绝/退回及批量停止点。参数、执行入口和验证范围见[泰国后续说明](thailand/README.md)。本次只读核验，不改真实订单。
