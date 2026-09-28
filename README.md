# ClipFlow — 视频剪辑网站（素材导入 → 剪辑 → 预览 → 导出）

Python 标准库后端 + ffmpeg 渲染 + React/Vite 编辑器。当前打通的核心流程：

- **素材导入**：上传视频（复制到 media/，绝不改动原文件），首次启动自动生成 3 段自带示例素材（红/绿/蓝，各 4 秒，含 440/550/660 Hz 提示音，便于核对边界与音画同步）。
- **单时间线剪辑**：画面与声音共用同一条轨道；每段可设入点/出点、速度（0.5–2×）、音量、调色；可拖动排序、删除。
- **位置级联（ripple）**：片段起点全部由顺序和时长推导（backend/timeline.py 的 Timeline.layout），不存储绝对位置。缩短或调速后，后续片段立即前移/后移。
- **预览即导出**：低清预览代理与最终成片由同一个 ffmpeg filter_complex 管线生成（backend/render.py），只有分辨率/码率不同，杜绝“预览一个结果、导出另一个”。
- **工程保存/重开**：工程以 JSON 原子写入 projects/，只引用素材 id 与裁切参数；重新打开后完整恢复。渲染前也会自动保存。
- **素材缺失**：缺失片段在素材库与时间线中标记，工程仍可打开和编辑；补齐同名素材后即恢复。缺失时导出被后端拒绝并给出明确错误。
- **导出中断**：渲染先写 exports/<id>.part，完成后原子改名为 .mp4；取消/失败会清理半成品，工程已保存，可直接重试。

## 运行

依赖：ffmpeg/ffprobe、Python 3.10+（仅标准库）、Node 18+。

    ./run.sh
    # 或手动：
    python3 backend/app.py                      # http://127.0.0.1:8000
    cd frontend && npm install && npm run dev  # http://127.0.0.1:5173

浏览器打开 http://127.0.0.1:5173 。

## API 摘要

- GET  /api/media — 素材列表（含 missing 标记）
- POST /api/media/import — multipart 上传，复制入库
- POST /api/media/samples — 生成/修复自带示例
- GET  /api/projects、/api/projects/<name> — 工程列表 / 打开（返回 missing_media）
- PUT  /api/projects/<name> — 保存工程（原子写）
- POST /api/render — 参数 kind=preview|export, project_name, timeline；先自动存工程
- GET  /api/render/<id> — 渲染进度（0–1）与输出 URL
- POST /api/render/<id>/cancel — 取消并清理半成品

## 测试

    python3 backend/tests/test_integration.py   # 渲染/工程/缺失/中断/级联，6 项
    cd frontend && npx vitest run               # 时间线布局，3 项

## 目录

- backend/timeline.py — 唯一时间线模型：片段、裁切、速度、layout() 级联计算
- backend/render.py — ffmpeg filter_complex；预览/导出共用；任务与原子发布
- backend/media.py — 素材库、ffprobe、上传复制、示例素材生成
- backend/projects.py — 工程 JSON 原子读写、缺失检测
- backend/multipart.py — 无第三方依赖的 multipart 解析（Py3.13 移除了 cgi）
- backend/app.py — stdlib HTTP API
- frontend/src/ — React 编辑器：素材库 / 预览 / 时间线 / 片段检查器
- media/ projects/ exports/ — 运行时数据（素材与工程分离）

后续扩展：字幕轨、转场（concat 之间插入 xfade）、更多调色节点都可以在 render.py 的 filter graph 上叠加，时间线模型保持单一事实来源。
