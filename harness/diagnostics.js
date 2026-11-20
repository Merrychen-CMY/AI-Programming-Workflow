// Only fixed messages and allowlisted metadata cross the local setup boundary.
const messages = {
  CONFIG_UNSAVED: '页面中的接入方式、账户或模型尚未保存，请先保存再测试，未发送模型请求。',
  CHATGPT_AUTH: 'ChatGPT请求未获授权，请核对所选账户和授权范围；不能仅凭状态码认定登录过期。不会切换到付费API。',
  CHATGPT_ACCESS_HTML: '接口返回了HTML访问拒绝页面，尚未取得OAuth错误码。请检查Harness后台到该接口的网络、代理及访问策略；浏览器登录成功不代表后台可访问。不会自动重试或切换付费API。',
  CHATGPT_CHALLENGE: '接口要求浏览器验证，Harness后台无法在此请求中完成。请检查访问策略；不会绕过验证、自动重试或切换付费API。',
  CHATGPT_ACCESS: 'OpenAI接口拒绝访问。请根据错误码核对账户权限、集成配置及访问策略；不要反复登录或改用付费API碰运气。',
  CHATGPT_CODE_EXPIRED: '本次授权码已失效或不被接受。请回配置页选原账户重新发起登录；不得刷新回调页重用旧授权码。',
  CHATGPT_REFRESH_INVALID: 'OpenAI确认续期凭据不可用，请对原账户重新登录；保留账户注册，不重复使用旧续期凭据。',
  CHATGPT_CLIENT: 'OpenAI拒绝客户端或授权参数，请核对插件的客户端注册和请求配置；重复登录不能替代配置修复。',
  CHATGPT_REGION: 'OpenAI返回访问地区限制，请核对官方支持地区和当前访问条件；不会切换计费来源。',
  CHATGPT_INELIGIBLE: '当前账户、工作区或策略不允许此应用使用订阅额度。请核对官方账户资格与应用权限，不重复发起相同授权。',
  CHATGPT_PERMISSION: '当前授权未允许此操作，请核对客户端及授权范围；不会自动更换账户或计费来源。',
  CHATGPT_RATE: '登录或模型目录请求受到速率限制，请稍后手动重试；不能据此认定会员额度耗尽。',
  CHATGPT_SERVICE: 'OpenAI登录或模型目录服务暂时不可用，已保留凭据。请稍后手动重试；不重放旧授权码。',
  CHATGPT_RESPONSE: '接口响应不符合预期，无法完成当前步骤；请核对响应类型及Harness网络，不据此清除登录凭据。',
  CHATGPT_IDENTITY: 'ChatGPT登录身份校验失败，未保存本次凭据。',
  CHATGPT_STATE: '登录回调已过期、已使用或不匹配，请重新发起登录。',
  CHATGPT_CONSENT: '尚未获得使用ChatGPT订阅的授权，请在官方登录页确认账户资格与权限。',
  CHATGPT_LOGIN_REQUIRED: '请先登录所选ChatGPT账户并授权订阅用量。',
  CHATGPT_BUSY: '另一项登录或续期正在处理；请稍后重试。若宿主曾中断，请核实旧进程后处理chatgpt-auth.lock。',
  CHATGPT_MODELS: '无法取得所选账户的模型清单，请重新登录并检查账户权限。',
  CHATGPT_TOOLS: '当前工具类型不支持会员接口，未发送请求。',
  CHATGPT_LIMIT: 'ChatGPT订阅用量或速率限制已触发。请在ChatGPT设置中查看用量；不会切换到付费API。',
  SETUP_INTERNAL: '本地配置操作失败，请重新打开配置页；若仍失败，检查插件与Harness凭据服务。',
  CONFIG_INVALID: '模型ID及Python/Codex路径不能为空；模型栏只能填写模型ID，路径栏不附带命令参数。',
  KEY_FORMAT: '密钥格式不正确：请完整粘贴API密钥，移除首尾空格和换行。',
  CONFIG_READ: '无法读取本地配置文件，请检查文件格式及读取权限。',
  CONFIG_WRITE: '无法保存本地配置文件，请检查目录写入权限。',
  CREDENTIAL_READ: '无法读取Harness凭据服务，请检查宿主凭据功能是否可用。',
  CREDENTIAL_WRITE: '无法写入Harness凭据服务，密钥尚未成功保存；请检查宿主凭据功能。',
  SETUP_REQUIRED: '请先保存模型和API密钥，再点击测试API。',
  API_AUTH: 'API身份验证被拒绝，请核对密钥是否有效，以及项目、组织或IP访问权限。',
  API_ACCESS: 'API访问被拒绝，请核对模型/项目权限、访问地区或网络访问策略。',
  API_REGION: 'API返回不支持当前访问地区，请核对官方支持地区及账户访问条件。',
  API_MODEL: '模型不存在或当前项目无权访问，请核对模型ID与项目可用模型。',
  API_QUOTA: 'API额度或支出上限已触发，请检查API账户余额及项目/组织限额。',
  API_RATE: 'API请求受到速率限制，请等待后再手动测试；插件不会自动重试。',
  API_LIMIT: 'API返回429，但未能区分额度和速率限制；请检查API额度与限额。',
  API_REQUEST: 'API拒绝了测试请求参数，请核对所选模型对Responses、工具调用和结构化输出的支持。',
  API_SERVER: 'API服务暂时出错，请稍后手动测试；插件不会自动重试。',
  API_HTTP: 'API返回未预期的HTTP状态，请根据状态码排查接口访问。',
  NETWORK_TIMEOUT: '连接或读取API响应超时，请检查Harness进程的网络、代理及防火墙。',
  NETWORK_DNS: '无法解析API域名，请检查DNS及Harness进程的网络配置。',
  NETWORK_TLS: 'API连接证书验证失败，请检查证书和网络代理；不要关闭证书验证。',
  NETWORK_CONNECT: 'Harness进程无法完成API连接，请检查网络、代理及防火墙。浏览器能访问不代表宿主进程能访问。',
  API_RESPONSE: 'API响应格式异常，未能完成兼容性检查；请核对网络中间服务和接口支持。',
  API_RESPONSE_SIZE: 'API响应超过检查大小上限，已停止读取。',
  API_OUTPUT_LIMIT: 'API已返回，但测试达到2048输出token上限，响应未完成。不能据此判定密钥无效；需调整探测预算或推理设置后再测。',
  API_INCOMPLETE: 'API响应未完成，未通过兼容性检查；可能已产生API费用。',
  API_REFUSAL: '模型拒绝了固定测试请求，未通过兼容性检查。',
  API_TOOL_FORMAT: '模型没有返回预期的工具调用，未通过工具调用兼容性检查。',
  API_JSON_FORMAT: '模型没有返回预期的JSON对象，未通过结构化输出兼容性检查。',
};
const stages = { configuration:'配置', credentials:'凭据服务', function_call:'工具调用测试', structured_output:'结构化输出测试',
  auth_exchange:'浏览器已返回本机 → 后台兑换登录凭据', auth_refresh:'后台续期登录凭据',
  auth_discovery:'获取OpenAI登录配置', auth_jwks:'获取身份签名公钥', auth_identity:'验证登录身份',
  auth_models:'获取账户模型列表' };
const endpoints = {
  auth_exchange:'https://auth.openai.com/api/accounts/oauth/token',
  auth_refresh:'https://auth.openai.com/api/accounts/oauth/token',
  auth_discovery:'https://auth.openai.com/.well-known/openid-configuration',
  auth_jwks:'https://auth.openai.com（登录配置指定的签名公钥接口）',
  auth_models:'https://api.openai.com/v1/models',
};
const authCodes = new Set(['invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
  'refresh_token_invalidated', 'refresh_token_reused', 'invalid_client', 'unauthorized_client', 'invalid_scope',
  'unsupported_country_region_territory', 'subscription_sharing_user_not_eligible',
  'subscription_sharing_route_not_supported', 'chatpass_v2_scope_not_authorized',
  'chatpass_v2_invalid_authorization_context', 'subscription_sharing_invalid_user',
  'subscription_sharing_user_unavailable', 'subscription_sharing_usage_unavailable']);
function safeAuthDetails(details) {
  const result = {};
  if (authCodes.has(details?.provider_code)) result.provider_code = details.provider_code;
  if (['json', 'html', 'text', 'empty', 'unreadable'].includes(details?.response_kind)) result.response_kind = details.response_kind;
  // Accept only the known hexadecimal request-ID shape, never arbitrary header text.
  if (typeof details?.request_id === 'string' && /^req_[a-f0-9]{32}$/.test(details.request_id)) result.request_id = details.request_id;
  return result;
}

export class SetupError extends Error {
  constructor(code, stage = 'configuration', httpStatus, details) {
    const safeCode = Object.hasOwn(messages, code) ? code : 'SETUP_INTERNAL';
    super(messages[safeCode]);
    this.code = safeCode;
    this.stage = Object.hasOwn(stages, stage) ? stage : 'configuration';
    this.httpStatus = Number.isInteger(httpStatus) && httpStatus >= 100 && httpStatus <= 599 ? httpStatus : undefined;
    if (details) this.details = safeAuthDetails(details);
  }
}

export function setupDiagnostic(error) {
  const safe = error instanceof SetupError ? new SetupError(error.code, error.stage, error.httpStatus, error.details) : new SetupError('SETUP_INTERNAL');
  const details = safe.details || {};
  const evidence = [endpoints[safe.stage] && `接口：${endpoints[safe.stage]}`,
    details.response_kind && `响应类型：${details.response_kind}`, details.provider_code && `错误码：${details.provider_code}`,
    details.request_id && `请求编号：${details.request_id}`].filter(Boolean).join('；');
  return { code: safe.code, stage: safe.stage, ...(safe.httpStatus ? {http_status:safe.httpStatus} : {}),
    ...(endpoints[safe.stage] ? {endpoint:endpoints[safe.stage]} : {}), ...details,
    error: `[${safe.code}] ${stages[safe.stage]}${safe.httpStatus ? ` / HTTP ${safe.httpStatus}` : ''}：${safe.message}${evidence ? `\n${evidence}。` : ''} 未启动产品任务。` };
}

export function chatgptAuthFailure(status, result, stage, details = {}, challenge = false) {
  const supplied = typeof result?.error === 'string' ? result.error : result?.error?.code;
  const code = authCodes.has(supplied) ? supplied : undefined;
  let category = 'CHATGPT_AUTH';
  if (challenge) category = 'CHATGPT_CHALLENGE';
  else if (status === 403 && details.response_kind === 'html') category = 'CHATGPT_ACCESS_HTML';
  else if (code === 'invalid_grant' && stage === 'auth_exchange') category = 'CHATGPT_CODE_EXPIRED';
  else if (stage === 'auth_refresh' && ['invalid_grant', 'invalid_refresh_token', 'token_expired',
    'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'].includes(code)) category = 'CHATGPT_REFRESH_INVALID';
  else if (['invalid_client', 'unauthorized_client', 'invalid_scope', 'subscription_sharing_route_not_supported'].includes(code)) category = 'CHATGPT_CLIENT';
  else if (code === 'unsupported_country_region_territory') category = 'CHATGPT_REGION';
  else if (code === 'subscription_sharing_user_not_eligible') category = 'CHATGPT_INELIGIBLE';
  else if (['chatpass_v2_scope_not_authorized', 'chatpass_v2_invalid_authorization_context'].includes(code)) category = 'CHATGPT_PERMISSION';
  else if (status === 403) category = 'CHATGPT_ACCESS';
  else if (status === 429) category = 'CHATGPT_RATE';
  else if (status >= 500) category = 'CHATGPT_SERVICE';
  return new SetupError(category, stage, status, { ...details, provider_code: code });
}

export function httpFailure(status, result, stage) {
  // Never echo error.message, arbitrary error codes, request IDs or response bodies.
  const code = result?.error?.code;
  const quota = ['insufficient_quota','credit_balance_exhausted','organization_spend_limit_exceeded','project_spend_limit_exceeded','organization_usage_limit_exceeded'];
  let category = 'API_HTTP';
  if (status === 401) category = 'API_AUTH';
  else if (status === 403) category = code === 'unsupported_country_region_territory' ? 'API_REGION' : 'API_ACCESS';
  else if (status === 404) category = 'API_MODEL';
  else if (status === 429) category = quota.includes(code) ? 'API_QUOTA'
    : ['rate_limit_exceeded','slow_down'].includes(code) || result?.error?.type === 'rate_limit_error' ? 'API_RATE' : 'API_LIMIT';
  else if (status === 400 || status === 422) category = 'API_REQUEST';
  else if (status >= 500 && status <= 599) category = 'API_SERVER';
  return new SetupError(category, stage, status);
}

export function networkFailure(error, stage) {
  const code = error?.cause?.code || error?.code;
  let category = 'NETWORK_CONNECT';
  if (['TimeoutError','AbortError'].includes(error?.name) || ['ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_HEADERS_TIMEOUT','UND_ERR_BODY_TIMEOUT'].includes(code)) category = 'NETWORK_TIMEOUT';
  else if (['ENOTFOUND','EAI_AGAIN'].includes(code)) category = 'NETWORK_DNS';
  else if (['CERT_HAS_EXPIRED','DEPTH_ZERO_SELF_SIGNED_CERT','SELF_SIGNED_CERT_IN_CHAIN','UNABLE_TO_VERIFY_LEAF_SIGNATURE','UNABLE_TO_GET_ISSUER_CERT_LOCALLY','ERR_TLS_CERT_ALTNAME_INVALID'].includes(code)) category = 'NETWORK_TLS';
  return new SetupError(category, stage);
}
