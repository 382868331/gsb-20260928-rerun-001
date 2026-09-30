# 视频剪辑网站（第一阶段：素材导入 → 剪辑 → 预览 → 导出）

Python(FastAPI) + React(Vite) 的最小可用剪辑台。画面和声音共用**一条时间线**：
片段顺序决定位置，每段时长 = `source_out - source_in`，后面的片段位置自动级联。
相邻片段可设置**交叠转场**（最长为相邻两段较短者的一半）：转场期间两段画面线性
溶解、声音等幅交叉淡化，每段还有独立音量（0–4）。交叠处总时长 = 各段之和减去转场。
预览页面与 ffmpeg 导出器调用同一个 `compute_timeline`，保证所见即所导。

## 功能

- 素材导入（上传到 `data/media/`，按 sha256 去重，**原始文件不会被覆盖**）
- 内置 3 段样例（红/绿/蓝画面 + 440/660/880Hz 声音，带源时间戳烧录）
- 多段视频裁切（入点/出点）、删除、前后调整顺序
- 交叠转场与逐段音量，改动立即生效并自动保存
- 统一时间线预览（双视频轨溶解 + WebAudio 音量/交叉淡化、拖动定位、逐帧 ±1/30s、
  转场前后 1s 循环试听；素材缺失时占位提示，工程仍可编辑）
- 工程保存为 `data/projects/<id>.json`（原子写入），重新打开后继续剪，版本号递增
- 后台 ffmpeg 导出 MP4：单条 filter graph 逐段 `trim/atrim` + `volume`，
  相邻段用 `xfade/acrossfade`（或硬切 concat）衔接，与预览完全一致
- 导出可中断（terminate 子进程，删除中间文件）；失败/取消不会破坏工程

## 运行

```bash
# 后端（首次启动自动生成样例素材到 ./data）
./backend/run_dev.sh            # http://127.0.0.1:8000

# 前端
cd frontend
npm install   # 如 node_modules 不存在
npm run dev   # http://127.0.0.1:5173 ，/api 代理到 8000
```

数据根目录可用 `EDITOR_DATA_ROOT` 环境变量修改（默认仓库下 `./data`）。

## 测试与验证

```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python backend/scripts/verify_export.py
```

`verify_export.py` 会导出一条已知时间线（1.5s + 2.5s + 1.5s），然后：

- `ffprobe` 核对成片总时长（含转场重叠：5.5s − 0.7s − 0.6s = 4.2s）；
- 在每段内部和切点之后抽帧取平均色，核对画面边界；
- 在转场中点抽帧，核对画面为两段颜色的 50/50 混合；
- 对导出音轨做 440/660/880Hz 频率能量检测，核对声音与画面同边界、
  转场中点同时存在两个音调、0.5× 音量段的能量约为 1/4；
- 取消后重新导出，核对时长与转场混合帧一致。

## 关键代码

- `backend/app/timeline.py`：唯一时间线计算（预览/导出共用）
- `backend/app/store.py`：工程 JSON 原子保存
- `backend/app/exporter.py`：ffmpeg 逐段裁切 + concat + 任务取消
- `frontend/src/store.ts`：编辑状态与本地时间线镜像（以后端 `/timeline` 为准）
- `frontend/src/components/Preview.tsx`：按时间线调度的统一预览
