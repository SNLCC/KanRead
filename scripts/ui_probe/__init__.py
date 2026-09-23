"""用真实浏览器验证界面行为（Edge/Chrome headless + CDP）。只在需要时跑，不进测试套件。

为什么需要它：本项目没有浏览器测试工具，界面问题只能靠用户目视确认；而"改了却没生效"
（浏览器跑着缓存的旧脚本）与"功能真的没做对"从用户描述上分不出来。第 33 轮就吃过这个亏：
用户两次反馈"批注弹窗无法拖动"，用这套探针在真实页面里一测，拖动与缩放其实都是好的，
真正的问题是页面没刷新。

用法（在仓库根目录）：

    # 1) 起一个临时后台（数据目录在 scripts/ui_probe/data，不碰 data/）
    .venv\\Scripts\\python.exe scripts/ui_probe/serve.py
    # 2)（可选）导入一份合成文献，好让阅读界面渲染出来
    .venv\\Scripts\\python.exe scripts/ui_probe/seed.py
    # 3) 起 headless 浏览器（需要能启动浏览器；本机沙箱下这一步要更宽的文件权限）
    #    见 ui_probe.md 里的完整命令
    # 4) 跑探针
    .venv\\Scripts\\python.exe scripts/ui_probe/drive_real.py       # 真实应用页面
    .venv\\Scripts\\python.exe scripts/ui_probe/drive.py            # 最小 DOM + 真实样式/代码片段

只用标准库（自带一个最小 WebSocket 客户端），不引入任何依赖；不改动应用代码。
"""
