[app]

# (str) 应用标题
title = 批量账号查询
# (str) 包名
package.name = accountquery
# (str) 包域（发布到商店前请改成你自己的域名，避免冲突）
package.domain = com.example

# (str) 源码目录（当前目录，含 main.py 与引擎模块）
source.dir = .
source.include_exts = py

# (str) 应用版本
version = 0.1
version.regex = __version__ = ['"](.*)['"]

# (list) 依赖：python3 + kivy + requests
requirements = python3,kivy,requests

# (str) 横竖屏
orientation = portrait

# 权限：联网查询 + 读取/导出本地 CSV
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

# 编译 / 最低 SDK
android.api = 33
android.minapi = 21
android.ndk = 25b

# 目标架构
android.archs = arm64-v8a

# 自动接受 SDK 许可（构建时需要）
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 0
