; ============================================================
; RSS_Todo 自定义 NSIS 脚本
; 用途：electron-builder 默认卸载器只删 %APPDATA%（Roaming）下的
; userData，删不到后端的 %LOCALAPPDATA%\RSS_Todo（app.db/cookie 在此）。
; 安全要求：卸载时必须彻底清除用户数据，防止 cookie/订阅残留。
; ============================================================

!macro customUnInstall
  ; 删除后端数据目录（app.db、downloads、log 等全部用户数据）
  RMDir /r "$LOCALAPPDATA\RSS_Todo"
!macroend
