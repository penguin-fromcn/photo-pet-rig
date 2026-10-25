# Windows 打包

需要 Windows + PyInstaller：

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\build_pet.ps1 `
  -Python .venv\Scripts\python.exe
```

`build_pet.ps1` 会：

1. 把 `examples/demo_character` 作为 `assets` 数据目录打进 `_internal/`；
2. 把 `packaging/pet_config.json` 放进 exe 同目录并另存为 `config.json`；
3. 用 `packaging/pet_entry.py` 启动 `photopet.pet.app.main`；
4. 输出到 `dist/pet/PhotoDesktopPet/`。

`pet_config.json` 是**演示配置**（`assets.directory = "assets"`）。换成自己的角色时：

- 把 `<slug>_pet_assets/` 放到 exe 同目录，改 `config.json` 的
  `assets.directory` 指向它；或
- 直接用源码版 `photopet pet --assets <目录>`。

构建产物不提交到 Git。PyInstaller onedir 包含 Qt 运行库，体积通常 100 MB 以上。
