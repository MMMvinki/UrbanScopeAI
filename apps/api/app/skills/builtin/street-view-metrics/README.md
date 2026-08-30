# 街景绿视率与天空可视因子

> **⚠ 当前状态：预留（数据源待接入）** — 全部链路已验证可跑，唯一待落地的是真实街景数据源的授权 key
> （百度需付费开通，`240`；腾讯免费但需开发者认证）。数据源可插拔：改 `scripts/panorama.py` 的 `fetch_panorama()` 即可接入任一家。
> 独立实测脚本见 [examples/fetch_and_segment_demo.py](../../../examples/fetch_and_segment_demo.py)。

沿路网布点，采集街景全景图，用 Cityscapes 预训练语义分割模型（fastseg）逐像素分类，
计算**绿视率（植被占比）**、**天空可视因子（天空占比）** 等类别占比，输出点图层上图。

- 取图：百度地图全景静态图 API `/panorama/v2`（`coordtype=wgs84ll`，平台 WGS-84 免坐标转换），
  需百度服务端 AK（`BAIDU_AK`，写 `apps/api/.env`，或 `execute_skill_script` 传 `baidu_ak`）。
- 分割：fastseg（MobileNetV3，Cityscapes 19 类预训练），CPU 推理，单图约 0.5~1.5s。
- 采样：沿路网每 `spacing_m` 布点（默认 50m）；`view_mode=quad`（4 朝向，默认）/ `full`（360°全景）。

运行契约见 `skills/store.py`：`--input in.json --output out.json`，产物图层写 `workspace/data/layers/<id>.json`，
经 output.json 声明上图。依赖声明在 `requirements.txt`，首次运行自动安装。
