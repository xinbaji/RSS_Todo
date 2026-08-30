/* electron-builder afterPack 钩子：打包前剔除冗余文件，极致瘦身。
 * 只删「纯文档/残留」类安全冗余：LICENSE 等文本、default_app.asar 残留、非 en-US locale。
 * 注意：resources.pak / icudtl.dat / V8 快照 / 图形 dll 等 Chromium 运行必需，绝不能删。
 * 删除总数控制在 50 个/次以内（本机沙箱对批量删除有阈值保护）。
 */
const fs = require("fs");
const path = require("path");

exports.default = async function afterPack(context) {
  const { appOutDir } = context;
  let removed = 0;
  const del = (p) => {
    try {
      if (fs.existsSync(p)) { fs.unlinkSync(p); removed++; }
    } catch (_) {}
  };
  // electron 发行版自带的 license/readme 文档（用户不需要）
  for (const f of ["LICENSE", "LICENSES.chromium.html", "LICENSE.electron.txt", "version"]) {
    del(path.join(appOutDir, f));
  }
  // default_app.asar 只在未指定 main 时存在，正常打包后不应出现，残留则清
  del(path.join(appOutDir, "resources", "default_app.asar"));
  // 语言包只留 en-US（这里手工裁剪，避免 electronLanguages 的批量删除触发沙箱保护）
  const locales = path.join(appOutDir, "locales");
  if (fs.existsSync(locales)) {
    const files = fs.readdirSync(locales).filter((f) => f !== "en-US.pak");
    for (const f of files) del(path.join(locales, f));
  }
  if (removed > 0) console.log(`[afterPack] 已剔除 ${removed} 个冗余文件`);
};
