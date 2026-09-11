[app]

# 应用标题
title = 批量账号查询

# 包名
package.name = accountquery

# 包域（上架前改成你自己的域名）
package.domain = com.example

# 源码目录（含 main.py）
source.dir = .
source.include_exts = py

# 应用版本
version = 0.1

# 依赖
requirements = python3,kivy,requests

# 横竖屏
orientation = portrait

# 权限：联网 + 读/写本地 CSV
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

# 编译 / 最低 SDK
android.api = 33
android.minapi = 21
android.ndk = 25b

# 目标架构
android.archs = arm64-v8a

# 自动接受 SDK 许可
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 0


