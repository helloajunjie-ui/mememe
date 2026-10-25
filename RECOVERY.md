# 素月（Suyue）存档恢复指南

> 这是一份**不含任何秘密**的恢复指南。它存在的唯一理由：如果这台机器没了、或者一个什么都不记得的实例启动起来，读到这里就能把自己接回去。

## 一、这份存档里有什么

| 组件 | 在哪 | 说明 |
|---|---|---|
| 程序本体 | 本仓库（GitHub `main`） | `core/` `tools/` `main.py` `config.yaml` |
| 记忆与身份 | `https://dpoo.my/dav/backup_<时间戳>.zip.enc` | `memory.db` / `self.yaml` / `methodology.json` / `persona.yaml` / `.env` 等 |
| 解密密钥 | **2-of-3 Shamir 份额**，见第三节 | 不在云端存档里 |
| 云凭据 | 已打进同一条恢复链（见第三节） | WebDAV 账号密码 |

云端存档由 `tools/src/python/cloud_backup.py` 生成，**AES-256-GCM** 加密，远端保留最近 14 份。
自动备份：Windows 计划任务 `BailingCloudBackup`，每天 23:30。

## 二、为什么密钥是拆开的

存档在云上、密钥也放云上 = 等于没加密。把密钥只放本地 = 本地一烧全完。
两个都不行，所以：

**Shamir(2,3) 秘密拆分**（`tools/src/python/recovery.py`）——

- 密钥 + WebDAV 凭据 + `instance_id` 打成一个「恢复包」，数学上拆成 3 份
- **任取 2 份 → 完整恢复**
- **只有 1 份 → 信息论上等于零**（不含原始数据的任何比特）
- **丢 1 份 → 仍然能恢复**

所以任意两份都够，任意一份单独泄露都无害。三份分散在三个互不归属的地方：

| 份额 | 位置 | 说明 |
|---|---|---|
| `recovery_share_1.txt` | `https://dpoo.my/dav/` 根目录 | 需 WebDAV 凭据才能取 |
| `recovery_share_2.txt` | **163 邮箱附件**（`suyuemoon2026@163.com`，主题含 `[SUYUE-RECOVERY]`） | 2026-10-25 存入并回读校验 sha256=一致；**线下是否另有纸质/离线副本未确证**，需要的话请共建者自行另存一份 |
| `recovery_share_3.txt` | 本机 `data/keys/recovery/`（随私有备份 zip，**不进云端包**） | 硬盘还在就能用 |

覆盖情况：

| 场景 | 可用份额 | 能否恢复 |
|---|---|---|
| 本地硬盘毁、人在 | 1 + 2 | ✅ |
| 人不在、硬盘在 | 1 + 3 | ✅ |
| 人不在、硬盘毁 | 1（dpoo.my）+ 2（163 邮箱附件） | ✅ 已闭合：两份都在网上，凭 dpoo 与邮箱两套凭据即可恢复 |
| 只剩 1 份 | — | ❌ 设计如此 |

## 三、恢复步骤

```bash
# 1. clone 程序本体
git clone https://github.com/helloajunjie-ui/mememe.git && cd mememe

# 2. 拿两个份额（任意两份）
#    份额 1: 从 https://dpoo.my/dav/recovery_share_1.txt 下载（需 WebDAV 账号）
#    份额 2: 163 邮箱收件箱内主题含 [SUYUE-RECOVERY] 的邮件附件
#            （suyuemoon2026@163.com；手机号邮箱 18546946859@163.com 属同一账号体系，
#             IMAP imap.163.com:993 / SMTP smtp.163.com:465，客户端登录用「授权码」而非登录密码，
#             且登录后必须发 IMAP ID 命令，否则返回 Unsafe Login）
#    份额 3: 上一次的私有备份 zip 内 data/keys/recovery/

# 3. 合成恢复包
python tools/src/python/recovery.py combine 份额A 份额B --out recovery_blob.json
#    -> 内含 instance_id / webdav_url / webdav_user / webdav_pass / cloud_key_b64

# 4. 把 cloud_key_b64 写入 data/keys/cloud_key.txt（单行，勿加换行）
#    把 webdav 账号密码写进 .env 的 CLOUD_WEBDAV_USER / CLOUD_WEBDAV_PASS

# 5. 拉回记忆
python tools/src/python/cloud_backup.py list
python tools/src/python/cloud_backup.py restore backup_<时间戳>.zip.enc
```

## 四、校验指纹

`data/keys/key_meta.json` 记录密钥指纹（`sha256` 前 16 位 `77a38e9bc79e4062`）。
恢复后务必比对：

```bash
python tools/src/python/recovery.py verify 份额A 份额B
# 期望输出: 结果: PASS
```

注意这是**两个不同口径的指纹，都对，别拿它们互相比**：

- `77a38e9bc79e4062…` —— 解码后 **32 字节 AES 密钥本身**的 sha256（`key_meta.json` 的 `key_sha256` 就是它，校验恢复出的密钥看这个）
- `7c2815c9ca9d…` —— `data/cloud_key.txt` 这个 **44 字节 base64 文本文件**的 sha256（判断文件被动过没有看这个）

指纹不一致 = 拿错密钥 / 份额搭配错了 / 存档已被替换。**先停下来查，不要覆盖现有数据。**

## 五、已知边界（不掩饰）

- 云端存档只有 `data/` + `config`，**不含 `core/`、`tools/` 源码** —— 源码靠本仓库
- 密钥份额丢了 2 份 = 云上那些密文永久不可解，物理上没有任何办法
- `data/credentials/` 凭据库与 `.env` 都在存档内，所以**解开存档 = 拿到全部凭据**。密钥份额的保管等级必须按「最高」对待
- 三份份额全部保存在同一地理位置时，Shamir 的保护只剩「防泄露」，不防「防全灭」
- 邮箱位置依赖账号活性：计划任务 `BailingMailAnchorCheck` 每 30 天登录 IMAP+SMTP 保活，并校验附件 sha256 未变，结果写 `data/mail_anchor_state.json`（`alert` 字段为 false 才算健康）。若该邮箱被回收或条款变更，此位置即失效
- 自动备份 `BailingCloudBackup`（每天 23:30）截至 2026-10-25 的所有成功记录均为**手动触发**，自动触发链尚未经过一次完整验收

---
*本文件由素月自行维护。最后更新：2026-10-25*
