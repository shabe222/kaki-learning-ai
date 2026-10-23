# 柿小吉 Live2D 正式模型接入口

本目录只接受 **Live2D Cubism Editor 真正导出的模型资产**。

正式入口固定为：

`kaki.model3.json`

该文件必须真实引用同目录中的 `.moc3`、textures、motions / expressions，以及存在时的 physics / pose / display-info。当前仓库故意不放置占位 `.moc3` 或假 `.model3.json`，因此在正式资产到位前 `KAKI_AVATAR_RUNTIME_V2` 会明确返回 `model_required` / `sdk_unavailable`，不会用 sprite、GIF、视频或代码重画角色冒充 Live2D。

模型参数与动作合同见最终交接包中的 `LIVE2D-ASSET-PRODUCTION-CHECKLIST.md`。
