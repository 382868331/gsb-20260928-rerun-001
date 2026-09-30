# 视频剪辑网站（第一阶段：素材导入 → 剪辑 → 预览 → 导出）

Python(FastAPI) + React(Vite) 的最小可用剪辑台。画面和声音共用**一条时间线**：
片段顺序决定位置，每段时长 = `source_out - source_in`，片段可设置与上一段的
**交叠转场**（画面 xfade、声音 acrossfade）和**独立音量**；拖动转场长度会缩短
整条时间线并级联后续片段。预览页面与 ffmpeg 导出器调用同一个 `compute_timeline`，
保证所见即所导。

## 功能

- 素材导入（上传到 `data/media/`，按 sha256 去重，**原始文件不会被覆盖**）
- 内置 3 段样例（红/绿/蓝画面 + 440/660/880Hz 声音，带源时间戳烧录）
- 多段视频裁切（入点/出点）、删除、前后调整顺序
- 片段间交叠转场（拖动片段左缘手柄或检查器滑杆，时间线总时长同步变化）
- 每段独立音量（0–200%），交叠区两侧声音按线性曲线混合
- 统一时间线预览（双层视频 + Web Audio 实时合成交叠与音量；素材缺失时占位提示，工程仍可编辑）
- 工程保存为 `data/projects/<id>.json`（原子写入），重新打开后继续剪，版本号递增
- 后台 ffmpeg 导出 MP4：单个滤镜图内逐段 `trim/atrim` 同一 PTS 区间裁切、按段调音量，
  再用 `xfade`/`acrossfade` 按时间线交叠合成
- 导出可中断（terminate 子进程并删除 `.part` 半成品）；失败/取消不会破坏工程，可立即重试
- 上传使用独立的 `data/staging/` 暂存目录（按需创建、用后即清），首次使用或清空临时
  素材后直接上传都能通过，不依赖任何预先操作

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

`verify_export.py` 会导出两条工程：硬切（1.5s + 2.5s + 1.5s）与交叠（三段 2s，
0.5s/0.8s 交叠，音量 1.0/0.3/1.4），然后：

- `ffprobe` 核对成片与每条流的总时长（交叠工程总时长 = 6 - 0.5 - 0.8 = 4.7s）；
- 在每段内部和切点之后抽帧取平均色，核对画面边界与首尾帧；
- 在交叠中点核对颜色混合与双音调同时存在；
- RMS 能量核对独立音量；
- 对导出音轨做 440/660/880Hz 频率能量检测，核对声音与画面同边界。

## 关键代码

- `backend/app/timeline.py`：唯一时间线计算（预览/导出共用）
- `backend/app/store.py`：工程 JSON 原子保存
- `backend/app/exporter.py`：ffmpeg 单图裁切/调音量 + xfade/acrossfade 交叠 + 任务取消
- `frontend/src/store.ts`：编辑状态与本地时间线镜像（以后端 `/timeline` 为准）
- `frontend/src/components/Preview.tsx`：按时间线调度的统一预览
