# 最终工程验证记录

后续2026-10-29的测评隔离、通用JSONL和及时采集修复见 [最新验收记录](ISOLATION-20261029.md)。以下为2026-10-25的历史发布验收，不应作为后续版本测试数量。

验证记录时刻（Asia/Singapore）：2026-10-25T01:47:02.036752+08:00。本轮通过命令行完成，没有再操作电脑窗口。

## 验证结论

| 项目 | 实际结果 | 证据（相对项目根目录） |
|---|---|---|
| Linux 全套 Python 回归 | 85 项，1 项平台跳过，16.165 秒，PASS | outputs/process-show/_qa/tests-final-linux.log |
| Windows 全套 Python 回归 | 85 项，3 项平台跳过，31.941 秒，PASS | outputs/process-show/_qa/tests-final-windows.log |
| JavaScript | 语法检查通过；导航几何7项断言通过 | tests/test_navigation.cjs |
| Linux 入口 | shell语法、服务状态、终端选择和实际测评端口通过 | outputs/process-show/_qa/linux-entry-selection.log |
| Windows 入口 | 从非项目CWD调用，仍绑定本项目；实际停止、重启，本轮不打开浏览器 | outputs/process-show/_qa/native-windows-entry-receipt.json |
| Windows 本机集成 | 隔离合成项目、目录API、选择测评、MP4生成和读取、停止/重启通过；视频38,802字节 | outputs/process-show/_qa/local-service-win32.json |
| 不同测评独立端口 | 真实R2R和RxR分别在28498/33693，任务列表互不混合 | outputs/process-show/_qa/run-port-receipt.json |
| 端口占用与数据边界 | 跳过占用端口，复用同测评，拒绝其他测评读/固定；停止一个不影响另一个 | tests/test_run_sessions.py |
| 客户端地址可达性 | Windows实际读取两地址页面；目录ID/测评ID/带令牌健康探测CORS均通过 | outputs/process-show/_qa/final-client-reachability.json |
| 兼容调试连接恢复 | 中断独立测试SSH传输，等待页HTTP200；恢复后同一公开URL可读，新PID | outputs/process-show/_qa/debug-bridge-reconnect.json |
| 缓存策略 | 默认3600秒、自定义60..2592000秒、边界、固定、重启、过期排除、下载租约通过 | tests/test_core.py、tests/test_run_discovery.py |
| 打包 | wheel构建、必要文件、旧导入路径及wheel实际导入通过 | outputs/process-show/_qa/package-receipt.json |
| 原测评源码 | 既有源码SHA与source-binding一致；下一轮补丁仅check、未应用 | outputs/process-show/_qa/run-port-receipt.json |

## 跳过项解释

Linux跳过的是Windows junction专用测试。Windows已实际验证junction无法越出最终根目录；Windows跳过的是POSIX符号链接测试与两个Linux端旧兼容配置测试。没有通过删测试、削弱校验或跳过有效失败来获得PASS。

## 本次连接拒绝的原因与修复

用户截图的28498最初仅在Linux服务主机监听，却被作为Windows可直接访问地址提供。当前调试连接已为各测评建立稳定的本地入口，并在返回链接前检查服务身份。断开传输时公开HTTP入口保留等待页。

正式三个入口仍只运行本机服务，不依赖SSH或跨系统映射。跨端口页面跳转前还会检查当前浏览器能否访问目标，并用scope_id/evaluation_id核对。跨域权限仅授予带测评专用随机校验值的localhost健康探测，数据接口保持同源与测评隔离。不能验证的地址不再被盲目打开。

## 明确未验证项

1. 没有Mac实机。macOS仅完成入口shell语法检查和共用Python逻辑/分支测试；原生启动、进程控制、字体和浏览器行为未做实机验收。
2. 没有切断整机网络或切换VPN；只中断项目独立测试传输，不把它称为所有网络故障都通过。
3. 按用户要求，本轮未操作窗口。最新浏览器点击导航与“另存为任意目录”对话框未重新点验；已验证相关HTTP内容、身份/CORS协议和JS语法。
4. 没有启动新的真实导航或GPU测评。历史缺图不能恢复；下一轮完整逐传感器采集、终止/reset时序、真实多楼层及浏览器端到端P95压力目标不在本轮实机通过范围。

## 最终目录与同步

总目录只有Linux/macOS/Windows三个启动文件；源码按capture/storage/playback/media/maps/service/web分组；compat保留旧公开导入；维护和旧方案不混入基本入口。

最终共享文件哈希和主机配置例外见 outputs/process-show/_qa/final-sync-receipt.json。Windows与Linux的service.json/sources.json按各自主机保留，不互相覆盖；当前调试连接的本机兼容profile也不作为跨主机配置发布。

此前阶段的验证、失败与修复过程保留在部署目录的 `docs/history/validation-before-final-pass.md`，不随通用源码发布，也不作为当前入口说明。当前用法见 [README](README.md)。

## 2026-10-25 发布前命令行复验

本节是后续重新执行的结果；全程没有浏览器或桌面窗口操作。

| 检查 | 实际结果 | 原始证据（部署项目根目录下） |
|---|---|---|
| Windows原生CMD完整流程 | 从不同CWD调用真实CMD启动；自动生成当前目录配置；目录弹窗API发现两次合成测评；API选择和终端编号选择后读取返回URL；不同端口；重复选择复用；跨测评读取拒绝；停止后控制进程和测评子端口关闭 | outputs/process-show/_qa/entry-flow-windows-20261025.json |
| Windows当前客户端真实测评 | 18765选择页HTTP200；选R2R后返回28498、选RxR后返回33693；从Windows实际请求返回页面、viewer.js、带令牌的健康探测和任务列表均成功；scope/evaluation/run身份一致，CORS正确 | outputs/process-show/_qa/entry-flow-client-20261025.json |
| Linux正式入口 | 执行真实sh的start/status/select，复用本目录已有服务；终端按编号选R2R，返回28498；显示日期、模型摘要、数据集、完成条数和过期剔除数 | 本轮终端输出及entry-flow-linux-20261025.json |
| Windows全套回归 | 85项，3项平台跳过，33.261秒，PASS | outputs/process-show/_qa/tests-release-windows-20261025.log |
| Linux全套回归 | 85项，1项平台跳过，16.759秒，PASS | outputs/process-show/_qa/tests-release-linux-20261025.log |
| 无私有配置的公开发布目录 | 仅拷贝显式发布清单，入口生成新根目录配置；Windows全套85项、3项平台跳过，34.251秒，PASS | outputs/process-show/_qa/tests-public-clean-windows-20261025.log |

原生Windows合成项目本次控制端口28932，两个测评端口21494/28755，验证结束后已停止并清除临时项目；这些临时端口不是常驻使用地址。当前客户端真实测评28498/33693另行验证，没有以Linux可访问代替Windows可访问。

新增 `tools/maintenance/verify_entry_flow.py` 留作可复验工具。第一次合成夹具缺少reset事件必需的位姿，导入未通过；补齐真实接口要求的reset/action位姿后重新执行通过，未改动导入器或绕过检查。旧连接模块的两组单元测试改为隔离配置夹具，不再读取发布者主机配置；原断线恢复、用户手动停止、PID所有权与端口约束断言保持。

通用发布使用显式文件清单：不包含本机service/sources/连接profile、评测原始数据、视频、环境、历史EXE及迁移记录。部署目录仍保留历史归档。Windows与Linux部署主机配置分别保留；同步逐文件核对SHA256。Mac实机、最新浏览器点击与另存为对话框、整机断网、真实新导航及GPU/P95仍未在本轮验收。
