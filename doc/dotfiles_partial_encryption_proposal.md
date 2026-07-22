# Dotfiles 局部（值级）加密技术方案（当前精简版）

本方案面向单用户、可信设备和可信仓库。Git clean/smudge filter 负责透明
转换：工作区保持明文，index 和提交中保存密文。日常使用仍是普通的
`git add`、`commit`、`push` 和 checkout。

## 规则

仓库根目录的 `rules.json` 是唯一的用户规则文件：

```json
{
  "*.json": {"keys": ["api_key", "password"]},
  "*.yaml": {"keys": ["aws_secret_key", "db_password"]}
}
```

规则只声明键名，不声明正则。实现仅支持简单的单行 JSON 双引号字段和
单行 YAML `key: value` 字段；不提供通用解析、转义、嵌套、注释或迁移。

## 密钥和密文

`dfm encrypt init RECIPIENT` 生成 64 字节 AES-SIV 数据密钥，并用外部
`gpg --batch --yes --encrypt --recipient RECIPIENT` 包装到已跟踪的
`.git-filters/key.gpg`。解密后的数据密钥只缓存于本机 `.git/line-crypt.key`；
过滤器只读取缓存，不在过滤过程中调用 GPG。删除该缓存即可移除本地访问权。

每个值使用固定密钥的 AES-SIV 确定性加密，编码为：

```text
ENCv1:<base64url-ciphertext>
```

clean 只替换规则匹配的值，已经是 `ENCv1:` 的值保持不变；smudge 将其还原。
缺少缓存或解密失败时过滤器以非零状态失败。

## 初始化和过滤器

用户先创建或编辑 `rules.json`；若文件不存在，init 可创建空文件。init 配置
`dfm-encrypt` 的 clean/smudge 命令（包含 `%f`），根据规则确保
`.gitattributes`，并暂存 `.git-filters/key.gpg`、`rules.json` 和
`.gitattributes`。只有 `dfm encrypt init` 和内部使用的
`dfm encrypt filter clean|smudge` 子命令；不维护 lock/unlock 状态。
