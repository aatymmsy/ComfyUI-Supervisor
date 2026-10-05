# 发布到 GitHub

上传工具生成的干净源码包，解压后将 comfyui-supervisor 文件夹里的文件放进仓库根目录。不要直接把正在使用的整个文件夹拖入网页；网页上传不会执行 .gitignore。

## 本机发布检查

在项目目录运行：

    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe tools/build_release.py --check
    .\.venv\Scripts\python.exe tools/build_release.py

最后一条生成 dist/comfyui-supervisor-0.1.0-source.zip，并显示 SHA-256。打包工具按明确的文件清单选择源代码、测试、模板、Schema、使用说明、许可证、CI 和两张已检查的界面截图。不会加入真实配置、上次任务、常用方案、工作流快照、数据库、生成图片、历史演示、未审阅截图、虚拟环境或日志。文本检查发现可疑密钥或个人目录时会拒绝打包，并只报告文件名与原因。

Git 忽略规则也排除上述运行文件；历史本机自测记录保留在本机。两个配置模板的服务/模型是占位值，云端模板默认禁用，示例工作流模型需要替换。

## 新安装验证

解压源码包到新的文件夹，进入项目根目录，双击 start.bat（或运行 start.ps1）。第一次应进入「必填配置」，任务列表为空。也可先执行下面的 CPU 演示，确认基本流程、数据库与交付，不调用云端或 ComfyUI：

    .\.venv\Scripts\python.exe -m supervisor demo
    .\.venv\Scripts\python.exe -m supervisor status

真实生图需要自己的 ComfyUI 和模型；自动规划与云端评审需要自己的 API 连接。模拟测试与 CPU 演示不能替代真实模型兼容性验证。

## 仓库说明

初版使用 0.1.0 / Beta，许可证为 MIT，署名为 ComfyUI Supervisor contributors，可在发布前改成你希望公开的署名。模型权重、LoRA、参考图片和第三方服务不适用本代码许可。README 已明确当前能力与限制。

建议 GitHub 描述：Python desktop app for ComfyUI prompt generation, image review, queued iteration and delivery.

建议 topics：comfyui、python、gradio、image-generation、workflow、prompt-engineering。

推送后 GitHub Actions 会在 Windows Python 3.11 / 3.13 上运行测试、检查 Schema 漂移、审核源码包并构建 wheel。首次远程 CI 是否通过需要推送后确认，本机测试不能代替该结果。项目以源码安装和本机单用户运行作为主要交付方式。
