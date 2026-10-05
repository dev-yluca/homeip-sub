# 家宽订阅（自动更新）

每小时自动从 VPN Gate 拉取最新家宽节点，生成 Clash/mihomo 可用的订阅文件。

## 订阅地址

```
https://raw.githubusercontent.com/dev-yluca/homeip-sub/main/jiakuan.yaml
```

直接填到 Clash、Stash、Shadowrocket（需配合对应客户端）或 Apple TV 的代理客户端里即可。

## 说明

- 需要 mihomo 内核 1.19.25+（用到 `dialer-proxy` 链式）
- 节点是 VPN Gate 志愿者共享的，掉线很正常，`🏠 家宽自动` 会自动切换
- 由 GitHub Actions 每小时自动更新
