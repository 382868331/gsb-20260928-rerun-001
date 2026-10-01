# 视频剪辑网站（第一阶段：素材导入 → 剪辑 → 预览 → 导出）

Python(FastAPI) + React(Vite) 的最小可用剪辑台。画面和声音共用**一条时间线**：
片段顺序决定位置，每段时长 = `source_out - source_in`，后面的片段位置自动级联。
预览页面与 ffmpeg 导出器调用同一个 `compute_timeline`，保证所见即所导。

## 功能

- 素材导入（上传到 `data/media/`，按 sha256 去重，**原始文件不会被覆盖**）
- 内置 3 段样例（红/绿/蓝画面 + 440/660/880Hz 声音，带源时间戳烧录）
- 多段视频裁切（入点/出点）、删除、前后调整顺序
- 源时间分段变速：一个片段内可先慢放/快放再恢复原速，视频和声音按同一素材时刻映射，原素材不被修改
- 统一时间线预览（含逐段静音、拖动定位；素材缺失时占位提示，工程仍可编辑）
- 工程保存为 `data/projects/<id>.json`（原子写入），重新打开后继续剪，版本号递增
- 后台 ffmpeg 导出 MP4：逐段按同一源时间区间 `trim/atrim`，视频重设 PTS、音频同步变速，再 concat/xfade 拼接；支持导出指定成片区间
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

- `ffprobe` 核对成片与每条流的总时长；
- 在每段内部和切点之后抽帧取平均色，核对画面边界；
- 对导出音轨做 440/660/880Hz 频率能量检测，核对声音与画面同边界。

## 关键代码

- `backend/app/timeline.py`：唯一时间线和速度段映射计算（预览/导出共用）
- `backend/app/store.py`：工程 JSON 原子保存
- `backend/app/exporter.py`：ffmpeg 逐段裁切 + concat + 任务取消
- `frontend/src/store.ts`：编辑状态与本地时间线镜像（以后端 `/timeline` 为准）
- `frontend/src/components/Preview.tsx`：按时间线调度的统一预览和 HTML 媒体变速
- `frontend/src/timeline.ts`：浏览器侧同源时间映射

## 边界规则

- 入点/出点不能越过原素材起止，也不能反向；变速段保存在素材时间轴上，缩短片段后区间外的安排自动移除。
- 每段变速只改变成片时长，不改变引用的源素材区间；总时长按 `(源区间长度 / 速率)` 重新计算。
- 转场按变速后的成片时长限制，最长为相邻两个片段较短时长的一半；速度调整导致时长缩短后，超限转场自动夹到允许范围。
- 拖放、跨速度边界循环和区间导出都以解析后的成片时间/素材时间映射为准。
