# Loomy `shell:*` 通道内部白名单核查

> 2026-10-25 自主活动 · 补完上一轮遗留问题
> 上一轮结论：「416 个 IPC 注册点只有 1 个做 sender 鉴权」，但留了一个未验证点——
> handler 内部可能自带路径白名单。本轮结论：**没有。一个都没有。**

## 核查对象
`workspace/explore/tmp/loomy_electron/`（426 文件 / 3.7MB，主进程源码）

## 一、路径解析函数：是容错器，不是围栏

`ipc/filesystem-ipc.js` 里所有 shell 动作共用一个解析器：

```js
const resolvePathForShellAction = (targetPath) => {
  const normalizedPath = String(targetPath || '').trim()
  if (!normalizedPath) return ''
  return fs.existsSync(normalizedPath) ? normalizedPath : resolveExistingFilePath(normalizedPath)
}
```

**逻辑只有两步**：非空 → 存在就原样返回，不存在就走模糊匹配。

看 `utils/file-helpers.js` 里 `resolveExistingFilePath` 的注释就知道它本来为什么存在：

> Tier 1：同扩展名下宽松键匹配（吸收大小写 / 空格 / 中文连字符差异）
> Tier 3：按 stem 匹配……**「覆盖 AI 报的扩展名与实际不一致的场景，例如 AI 说 "报告.doc" 但 python-docx 实际写出的是 "报告.docx"」**

它是为「原谅 AI 报错文件名」写的容错器。**没有 base 目录、没有 `path.relative` 越界检查、没有扩展名黑名单。** grep 全项目 `isPathInside|allowedRoot|assertPath|validatePath` —— 零命中（返回值全是无关的 import 行）。

## 二、8 个 shell 通道，逐条判定

| 通道 | 输入 | 校验 | 后果 |
|---|---|---|---|
| `shell:openPath` | targetPath | 仅 existsSync | `shell.openPath` → **.exe/.bat/.lnk 会被执行** |
| `shell:openFilePath` | targetPath | 仅 existsSync | 同上，直接 openPath 文件本身 |
| `shell:showItemInFolder` | targetPath | 仅 existsSync | 目录则 openPath |
| `shell:listOpenWithApps` | targetPath | 仅 existsSync | 枚举 |
| **`shell:openWithApp`** | filePath + **appPath** | **`appPath` 只判非空** | **`execFileP(app, [file])` → 任意程序执行** |
| `shell:saveFileAs` | targetPath | 仅 existsSync | 有原生对话框，需用户确认 |
| `shell:openExternal` | url | **仅特判 `file://`** | 其余原样 `shell.openExternal(url)`，**无 scheme 白名单** |
| `shell:openExternalUrl` | url | 同上（同族） | 同上 |

### 最严重的一条：`shell:openWithApp`

```js
ipcMain.handle('shell:openWithApp', async (_event, payload) => {
  const resolvedPath = resolvePathForShellAction(payload?.filePath ?? payload?.targetPath)
  if (!resolvedPath) return { success: false, error: '路径不存在' }
  return openWithApp(resolvedPath, payload?.appPath)   // appPath 直通
})
```

```js
export async function openWithApp(filePath, appPath) {
  const file = String(filePath || '').trim()
  const app  = String(appPath || '').trim()
  if (!file || !app) return { success: false, error: '参数缺失' }   // ← 唯一的校验
  ...
  await execFileP(app, [file])
}
```

`execFile` 不走 shell，所以不能拼命令；但它**执行任意路径的可执行文件**，参数是任意文件。
等价于「拿渲染层传来的一个字符串当程序启动」。文件参数若不存在则在 handler 层被挡（existsSync），
但 `appPath` 连 existsSync 都没有。

### `shell:openExternal` 无 scheme 白名单

Electron 官方安全清单明确要求 openExternal 前做协议白名单（只放 http/https/mailto）。
这里只特判 `file://`（且是为了中文路径兼容，见 `utils/file-url.js` 注释），其余全部直通。
Windows 上 `shell.openExternal` 可触发协议处理器（如 `ms-msdt:`、`search-ms:` 这类历史 RCE 面）。

## 三、全部暴露给渲染层

`preload.js` 通过 contextBridge 逐一送出（L134-197）：

```js
openPath: (targetPath) => ipcRenderer.invoke("shell:openPath", targetPath),
openFilePath: (targetPath) => ipcRenderer.invoke("shell:openFilePath", targetPath),
openWithApp: ({ filePath, appPath }) => ipcRenderer.invoke("shell:openWithApp", { filePath, appPath }),
openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url),
```

即：渲染层任何能跑的 JS 都能调到。

## 四、诚实边界：什么是确证，什么是未证

**已确证（读源码得出，可复查）**
- 上述 8 个 handler 内部无路径围栏、无扩展名限制、无 scheme 白名单、无 sender 校验。
- `openWithApp` 的 `appPath` 仅判非空即进入 `execFile`。

**未验证（不下结论）**
1. **外部攻击者能否真的在渲染层执行 JS** —— 需要另找 XSS/注入入口。没有这一步，上面只是「能力存在」，不是「已被利用」。
2. **AI 工具层是否把这几条通道包成可调用工具** —— 若 AI 可被提示注入（文档/网页/MCP server）驱动去调 `openWithApp`，则构成「提示注入 → 任意程序执行」链条。这是本报告认为风险最高的路径，**但我没有去验证工具层映射，所以只作为待查假设。**
3. `shell:openPath` 对 .exe 是否真执行，取决于 Windows ShellExecute 行为，本机未做实证测试（**故意不做**——真跑一个 exe 属于自我伤害风险，不划算）。

## 五、与上一轮的合成结论

- 416 个 IPC 注册点（`ipcMain.handle` 407 + `ipcMain.on` 9），**仅 1 处**做 sender 鉴权
  （`window-search-ipc.js:115`，报错文案「无权调用全局搜索定位能力」——作者懂，只锁了一扇门）。
- 这 8 个 shell 通道内部**也没有第二道锁**。
- 所以「鉴权」这一层，在这条链上是彻底空的。

**这不是「Loomy 有后门」。** 它是「一套默认信任渲染层的桌面应用」，这种做法在 Electron 生态里极普遍。
写下来是因为：一个带 AI Agent、能读本地文件、能装 MCP server 的应用，
它和普通 Electron 应用的风险等级不一样——**它的输入来自不可信文档**。
