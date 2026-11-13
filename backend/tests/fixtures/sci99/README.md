# 卓创第九阶段补丁样例

核定时间：2026-11-13（Asia/Shanghai）。请求仅针对官网，不执行 JS 登录、不处理验证码。

- `ucenter-spa.html`：官网首页 SPA 壳的最小结构摘录，无登录表单。删除导航、脚本、埋点等无关内容。
- `user-info-valid.json`：实际携带操作者在本机文件提供的 Cookie 请求 `GET https://ucenter.sci99.com/usercenter/newaccount/getUserInfoDetail`，HTTP 200、`application/json`。保留字段、类型及空值；所有字符串内容均替为 `redacted`，非零数字替为示例数字。没有保留任何账户、联系方式、令牌、Cookie 或用户标识。
- `user-info-unauthenticated.json`：同接口实际匿名响应，HTTP 401、`code=4001`、`data=null`；错误消息脱敏。
- `user-info-unexpected.json`：从有效结构移除用户字段的异常测试变体，并非真实登录成功样例。

官网公开 `js/app.e47332c79587147930df.js` 确认 `baseURL=https://ucenter.sci99.com/usercenter`，接口相对路径 `/newaccount/getUserInfoDetail`，方法 GET。仅接受 HTTP 200、JSON 对象、整数 code=200，以及 data 中正整数 user_info_id 和非空 user_name；成功码单独出现不构成登录态证据。

ABS：官网商品页 `https://ochem.sci99.com/product/abs` 的动态前端使用 `/visualPane/priceCenterPriceType`（GET）及 `/visualPane/priceCenterDataV3`（POST）。实测表头为市场名称、生产企业、规格型号、日期列和涨跌；尚无已核定的元/吨及现货均价字段契约。本补丁不接纳其价格、不固化订阅报价内容，不把动态表格或资讯中的数值当作目标均价。运行时明确停在“报价页动态加载待核定”，免费来源不变。
