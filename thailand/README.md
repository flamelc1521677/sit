# 泰国线下大额接口自动化

Python + Pytest + Requests。泰国独立使用OB5申请、chengzi人审；复用项目公共SSO、OCR工具、只读MySQL连接和原子状态工具。脚本、账号配置及运行状态与墨西哥隔离。

## 场景与VS Code入口

| 入口 | 功能 |
| --- | --- |
| TH00 | 登录→新增用户，不包含借款或放款 |
| TH01 | SSO HTTP200、code200、result.success，OAuth及泰国账号/应用范围 |
| TH02 | 固定OB5的lcline1，递增手机号，新增与API/数据库回查 |
| TH03 | 固定图片OCR→递增证件号并保存→递增KBANK银行卡并保存 |
| TH05 | 先试算，再携带本次费用及三期计划提交一次 |
| TH07 | chengzi对本用户唯一待审借款执行人审通过 |
| TH08 | 同一借款的单笔放款单审批成功，核验合同/借款/转账同步 |
| TH09 | 新增一位用户→OCR→KBANK→试算借款→人审通过→放款成功 |

打开api-tests项目，按Ctrl+Shift+D，在顶部下拉选择对应TH入口，按F5。TH09会要求输入本次唯一运行标识，如th_full_20261028_01；TH02、TH00和TH09会实际新增用户。调试控制台逐步显示场景和校验过程，同时保存日志。

终端从项目根目录运行，无需激活PowerShell：

```powershell
# 只登录
.\.venv\Scripts\python.exe -m pytest -c thailand/pytest.ini thailand/tests/01_login/test_th_login.py -v -s -x

# 新增到放款成功，只新增一位
.\.venv\Scripts\python.exe thailand/run_full_flow.py --run-id your_unique_th_run_id
```

单场景使用`python -m pytest -c thailand/pytest.ini <对应thailand/tests脚本> -v -s -x`。普通TH00/TH02不会借款或放款；TH03只处理当前已保存的最新泰国用户。

## 固定业务参数

- 申请账号：OB5（数据库规范登录名ob5，用户1593，部门11804）。员工固定启用的第一条Line：配置65 / lcline1 / ob516772222；不自动切换其他员工或Line。
- 手机号：0650100001起，每次按整数+1并保留开头0。只读明确发现已占用时记录原用户ID，不发新增请求，检查下一个号码，最多检查20个。实际验证新增接口接受明文手机号，虽页面仍提示hashid。
- 证件图片：thailand/assets/identity_front.png，来自用户提供的泰国证件。OCR识别后保留姓名及其他资料，证件号码1551500010011起，每次+1，不使用墨西哥的生日日期段。
- 银行：固定KBANK。银行卡号102519931001起，每次+1。
- 产品：线下大额产品001399；3500；3期；每期7天；总利息100；服务费1%；展期费率1%；罚息率10%。配置位于config/loan_application.json。
- 人审：chengzi，用户1572。TH07为pass，TH07.2为reject，TH07.3为close；一次只选一种结果，禁止OB5审批自己提交的申请。未擅自更改风控或人审mock开关。
- 时间：放款页面使用Asia/Bangkok，数据库使用Asia/Shanghai，显式转换并校验，不继承墨西哥时区。

## 凭据、状态与防重复

账号密码自动读取Windows当前用户环境中的TH_SSO_USERNAME/TH_SSO_PASSWORD及TH_APPROVER_USERNAME/TH_APPROVER_PASSWORD；不需要每次输入，不写入代码、VS Code配置或日志。默认本机配置优先于进程遗留值；显式进程覆盖使用TH_CREDENTIAL_SOURCE=process并提供完整配对。数据库仍由common/database.py读取本机SIT_DB_PASSWORD，只执行SELECT。

.state/thailand/下独立保存phone.json、identity_number.json、bank_card.json、latest_user.json及各场景attempts。号码在请求前持久化递增，不因超时回退。runs/保存完整运行进度，artifacts/保存逐场景日志，已有日志不覆盖。成功后last_full_flow_result.json保存结果。

TH09在新增前实际登录OB5和chengzi，核验图片与泰国库表。后续步骤绑定本次用户ID/UUID；最新用户被其他流程改变时，在业务请求前停止。相同运行标识不自动重建；存在未确认进度时不允许更换标识绕过。仅明确的登录验证码9999最多重新登录三次；密码错误、未知新增/借款/审批/放款结果均不自动重发。

已明确返回成功的资料保存允许只读回查原结果后继续，原OCR和保存请求不重发。已成功放款只读验证，不重复编辑。系统自动审批/放款时只有在完整成功链路验证后才记录为系统路径，不冒称chengzi进行了人工审批。续跑参数仅供已完成只读审计的特定检查点使用；不能用来跳过未确认操作。

## 泰国数据库映射

- off_biz_thailand.t_mgt_user：用户、手机号密文、OB5及部门/Line。数据库脱敏格式为首1位+末4位，列表展示为首3位+末3位，两者独立验证，密文仍精确匹配。
- t_mgt_user_aadhaar_card_info：原OCR号码、姓名及图片。
- t_mgt_user_compose_info：手工保存后的证件和个人信息。与详情完整密文精确匹配；API明文号码匹配本次输入，数据库脱敏值匹配修改后的号码，并关联原OCR图片。不能把原OCR字段当成手工修改后的号码。
- t_sys_bank_card：KBANK、卡号密文/脱敏、默认标志，另以API明文回查精确验证输入卡号。
- off_th_saas_loan：借款主单、预计划、收款卡、合同及正式三期计划。
- off_saas_saas：账号与人审、操作日志。泰国详情没有expectedAmount输入框，expected_amount可为空；泰国仍严格验证contract_amount=3500、状态、版本、chengzi及唯一USER/PASS流水，墨西哥校验保持不变。
- off_th_saas_borrower / off_th_saas_payout：申请、转账、放款单及成功操作流水。

所有当前和后续场景都必须完成对应数据库业务断言，不能仅测试连接或只看HTTP200。后续泰国场景继续放在本目录，不复用墨西哥业务状态或日期写入工具。

## 2026-10-27 实跑结果

本次只新增1位：172555 / 0650100002（0650100001属于此前用户172554，未重复创建）。身份证1551500010011和KBANK银行卡102519931001保存及回查通过；3500、三期、每期7天、总利息100，先试算后提交；chengzi人工通过；OB5将关联放款单审批成功。

合同C1320261027161023X3S6e；放款单06c9d399114b43a0bdbae8eab669a56b；实际放款3465。正式三期本金合计3500、利息合计100，成功状态、操作人和流水均已核验。初次对照墨西哥字段时的校验失败已通过泰国真实表/字段映射修正并只读恢复，未重复新增、上传、保存证件、提交借款或审批。

下一手机号0650100003，下一证件号1551500010012，下一银行卡号102519931002。最终审计：artifacts/th_full_20261027_01_final_audit.json；运行状态：.state/thailand/runs/th_full_20261027_01.json。本地规则测试200 passed，六个泰国场景可正常收集。


## 后续能力：复借、预提醒、逾期与催收

后续全部使用泰国独立配置、TH_前缀参数及.state/thailand/记录，不读取墨西哥状态。分单接收人固定ob5/1593，部门11804。普通场景数据库仍只读；逾期造数专用thailand/overdue_fixture.py仅允许sit-db01.suxi-test.com:3306的off_th_saas_loan，仅写明确合同、期次和记录ID的四个日期字段；不允许改本金、罚息金额、合同状态、账龄或逾期天数。

| VS Code入口 | 能力 | 是否实际修改数据 |
| --- | --- | --- |
| TH07.2 / TH07.3 | 指定用户人审拒绝 / 退回信审 | 实际审批一次，不改变已审批结果 |
| TH09.1 | 新增并完善资料，不借款 | 实际新增和保存资料 |
| TH09.2 | 新增至借款提交，不人审 | 实际新增、保存资料、试算并提交 |
| TH09.3 | 新增至人审通过，不放款 | 实际处理并由TH16只读证明未放款 |
| TH09.4 | 批量新增1～20位，可选上述停止点或放款 | 实际执行，逐人保存结果 |
| TH10 | 指定用户复借，保留原历史 | 只试算/提交新申请，不默认审批或放款 |
| TH11 | 生成T0/T-1/T-2预提醒 | 默认只读，选择0实际执行 |
| TH12 | 预提醒精准转交OB5 | 默认只读，选择0实际转交 |
| TH13 | 指定首个未还期次生成逾期X天及罚息 | 默认只读，选择0实际执行 |
| TH13.2 | 已逾期单追加罚息 | 默认只读，选择0实际执行 |
| TH14 | 正常流程更新已有逾期单天数 | 默认只读，选择0实际执行，不执行罚息任务 |
| TH15 | 催收单精准转交OB5 | 默认只读，选择0实际转交 |
| TH16 | 证明chengzi人审通过且未放款 | 只读 |
| TH17 | 已放款合同到逾期单的初始预检 | 只读 |
| TH17.1 | 已放款合同→预提醒→OB5分单→逾期罚息→催收分单 | 实际执行，需唯一请求标识 |
| TH18 | 新增→资料→借款→人审通过→放款→预提醒分单→逾期及催收分单 | 实际新增和执行，需唯一运行标识 |

F5运行时按提示填写手机号、合同、期次、目标天数。TH11～15的“1=只读预检；0=实际执行”默认选1。多笔合同时必须填写合同号，不自动选择最近一笔。TH10必须同时填手机号、用户ID、唯一申请标识；后续TH07和TH08继续填写同一手机号，显式操作现有用户不会改写最新新增用户状态。

TH17只验证当前已放款合同、任务、日期、账龄和接收人等初始条件，不能在尚未生成预提醒时宣称后续转交/入催已经验证。TH17.1执行时会在每一步产生数据后继续校验真实表单、业务记录及操作流水；已有有效预提醒会复用，不重复生成。默认最后转交催收单给OB5，终端可用--skip-collection-assignment止步于入催和罚息。

### 常用终端命令

从api-tests目录运行，不需要激活PowerShell虚拟环境：

```powershell
# 现有已放款用户：只读预检，不改期、不执行任务
.\.venv\Scripts\python.exe thailand/run_to_overdue.py --phone 0650100002 --contract C1320261027161023X3S6e --period 1 --days 1

# 同一类目标：实际生成逾期1天，必须使用本次唯一标识
.\.venv\Scripts\python.exe thailand/run_to_overdue.py --phone 你的手机号 --contract 你的合同号 --period 1 --days 1 --request-id 本次唯一标识 --execute

# 新增一位直到逾期15天（会实际新增、借款、放款和改期）
.\.venv\Scripts\python.exe thailand/run_full_flow.py --run-id 本次唯一标识 --overdue-days 15

# 批量新增5位，止步于chengzi人审通过，核验未放款
.\.venv\Scripts\python.exe thailand/run_batch.py --batch-id 本批次唯一标识 --count 5 --stop-after review
```

批次停止点为profile、submission、review或payout。相同批次/运行标识拒绝重建；未确认进度必须先核对原用户，不能删状态或换标识绕过。全流程会绑定运行标识、用户ID/UUID，发现最新用户被其他流程替换时在业务操作前停止。

单场景终端环境变量示例：

```powershell
# 指定合同第一期生成逾期10天：先预检
$env:TH_OVERDUE_CONTRACT_ID='你的合同号'
$env:TH_OVERDUE_PERIOD='1'
$env:TH_OVERDUE_DAYS='10'
$env:TH_OVERDUE_MODE='create'
$env:TH_OVERDUE_DRY_RUN='1'
.\.venv\Scripts\python.exe -m pytest -c thailand/pytest.ini thailand/tests/13_overdue/test_th_generate_overdue.py -v -s -x

# 确认目标后实际执行：同一请求重跑保留原标识，不自动重发未知操作
$env:TH_OVERDUE_REQUEST_ID='本次唯一标识'
$env:TH_OVERDUE_DRY_RUN='0'
.\.venv\Scripts\python.exe -m pytest -c thailand/pytest.ini thailand/tests/13_overdue/test_th_generate_overdue.py -v -s -x
```

直接执行TH13前，订单必须已完成TH11预提醒及TH12分单。无预提醒的已放款合同优先使用TH17/17.1。

### 单场景参数对照

| 场景 | 主要参数 |
| --- | --- |
| TH10 | TH_TARGET_PHONE、TH_REPEAT_USER_ID、TH_REPEAT_REQUEST_ID |
| TH11 | TH_PREALERT_PHONE、TH_PREALERT_CONTRACT_ID、TH_PREALERT_PERIOD、TH_PREALERT_DAYS、TH_PREALERT_DRY_RUN |
| TH12 | TH_PREALERT_ORDER_ID或TH_PREALERT_ASSIGN_CONTRACT_ID+TH_PREALERT_ASSIGN_PERIOD；TH_PREALERT_ASSIGN_DRY_RUN |
| TH13/13.2 | TH_OVERDUE_CONTRACT_ID+TH_OVERDUE_PERIOD或TH_OVERDUE_ORDER_ID；TH_OVERDUE_MODE=create/penalty_only；TH_OVERDUE_DAYS；TH_OVERDUE_PENALTY_DAYS；TH_OVERDUE_REQUEST_ID；TH_OVERDUE_DRY_RUN |
| TH14 | TH_UPDATE_OVERDUE_COLLECTION_ID或TH_UPDATE_OVERDUE_CONTRACT_ID+TH_UPDATE_OVERDUE_PERIOD；TH_UPDATE_OVERDUE_DAYS；TH_UPDATE_OVERDUE_REQUEST_ID；TH_UPDATE_OVERDUE_DRY_RUN |
| TH15 | TH_COLLECTION_ORDER_ID或TH_COLLECTION_CONTRACT_ID+TH_COLLECTION_PERIOD；TH_COLLECTION_DRY_RUN |
| TH07/08/16 | TH_TARGET_PHONE；空值仅按最新泰国用户定位，完整流程始终绑定本次用户 |

预提醒/催收记录ID与REM业务单号不同：传给TH15的是rem_bill.ID；脚本读取真实转交表单时才使用REMIND_BILL_ID，不混用。手机号多合同、多期或历史重复记录无法唯一定位时停止；不扩大SQL条件、不选第一笔凑结果。

### 时间、任务、账龄与罚息

应还日期和逾期天数基于服务端Asia/Bangkok时间，不使用电脑日期。逾期X天的应还日为服务端日期-X天，业务时间01:00，对应Asia/Shanghai存储时间02:00；催收入催日期为应还日期+1天。更新天数时先在受限事务内修改两张应还日期及rem_bill.OVERDUE_DATE，再执行更新任务，不直接写OVERDUE_DAYS/REMIND_GRADE。

| 泰国任务 | ID | 参数 |
| --- | --- | --- |
| executePreAlertListTimer | ca13c67e339a4bbdbd67dc3cf50b1143 | 当前2；仅支持0/1/2，对应T0/T-1/T-2 |
| executeCreateRemTimer | 336c762ee4b9489a9261df45a1cb72c3 | 空 |
| executeNewPenaltyTimer | 6861381967d8437b922f385b7c010c0f | 200 |
| updateRemindOverdueDays | 35341f84c58d446dbdb7b58cae52416c | 0 |

每次执行前核验beanName、methodName、dbGroupId=1、targetGroup=1和真实参数，禁止执行墨西哥targetGroup=2任务。预提醒默认跟随当前参数；明确填0/1/2时，先按真实任务编辑协议保存并核验参数，再按参数计算应还日。任务均为泰国共享任务，可能处理其他符合条件的泰国订单，不是按手机号局部执行。

当前泰国实际账龄为1天S0+、2～3天S1+、4～60天S2+，更长天数按真实rem_grade配置查找，不写死用户示例中的通用S1/S2编码。

新建逾期只执行一次罚息任务，校验实际罚息增长、时钟推进、API与数据库一致；未预设“每次只算一天”的公式。TH13.2中的轮数是任务执行轮数，不等于逾期天数，仅在已有罚息后允许限定修改罚息更新时间；不增长或不符合期望立即停止。TH14只更新逾期日期/天数，不代表已补齐罚息，也不承诺账龄迁移完成。

### 本次验证范围

2026-10-27：已只读核实泰国四个任务、真实账龄、OB5接收人、两类转交表单及库表；TH11预提醒预检、TH17初始串联预检、TH12预提醒分单预检、TH15催收分单预检通过。历史样本的重复还款期次和多催收单被TH13/14前置检查拦截，未为验证修改这些历史数据。

本次没有新增用户、提交借款、审批、修改日期、执行任务或实际转交；TH13～18的写入和任务结果需在用户指定符合条件的目标后实跑校验，不能把只读预检当作已生成逾期。此前TH09实际放款成功的记录仍保留。

本轮最终验证：215项本地规则测试通过；14个泰国场景可收集；25个泰国VS Code入口及输入引用校验通过。此前已放款用户172555的合同、三期日期本息和三套递增计数器保持不变。汇总见artifacts/th_followup_20261027_validation.json。
