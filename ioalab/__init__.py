"""ioalab：九问IoA仿真平台（Jiuwen IoA Lab）。设计见 设计/，运行与部署见 README.md 与 设计/部署.md。

对 a2c SDK 只 import a2c（vendor/wheels 里的 wheel）；A2X（vendor/A2X-registry 子模块）与 openJiuwen（openjiuwen）作库使用。
"""
from __future__ import annotations

from pathlib import Path

PKG = Path(__file__).resolve().parent          # <repo>/ioalab
ROOT = PKG.parent                              # 仓库根：data/ · 设计/ · vendor/ · deploy/ 都在这里
