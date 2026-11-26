// 登录揭幕动画(ZLoginReveal)专属文案。role 由调用方传入已翻译的身份显示名
// (App.vue 用 home.positions.* 解析),这里只负责拼接「你好，xxx」「身份：xxx」。
export const zhCN = {
  reveal: {
    fallbackName: '同学',
    hello: '你好，{name}',
    role: '身份：{role}'
  }
}
