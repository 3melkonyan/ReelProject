/* Load the real Armenian-default message layer into isolated browser fixtures. */
const fs = require('fs'), path = require('path'), vm = require('vm');
exports.install = (context, language = 'hy') => {
  const root = path.resolve(__dirname, '..');
  context.window ??= {};
  context.window.REEL_I18N = {language, messages: JSON.parse(fs.readFileSync(path.join(root, 'reel/locales', language + '.json'), 'utf8'))};
  context.document ??= {};
  context.document.documentElement ??= {};
  context.document.addEventListener ??= () => {};
  vm.runInContext(fs.readFileSync(path.join(root, 'reel/web/static/i18n.js'), 'utf8'), context);
};
