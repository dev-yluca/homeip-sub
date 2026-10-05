#!/usr/bin/env python3
"""从 VPN Gate 拉取家宽节点，生成 mihomo 可用的 jiakuan.yaml。
输出格式与 Cloudflare Worker 的 ?target=vg 保持一致。
"""
import base64
import csv
import re
import sys
import urllib.request

UUID = "fcd5b2fb-2a7b-4536-bfb9-288a8411d9d3"
DOMAIN = "jiakuan.moreyu-09.workers.dev"
VPNGATE_API = "https://www.vpngate.net/api/iphone/"
MAX_NODES = 80  # 最多取多少个节点

HEADER = """# 家宽订阅：CF 节点带路，落地是住宅宽带
# 内核要 1.19.25 以上，老内核不认这类节点
# 节点是网友共享的，掉线很正常，家宽自动会自己往下换
# 由 GitHub Actions 每小时自动更新
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false
unified-delay: true
tcp-concurrent: true
external-controller: 127.0.0.1:9090
dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver:
    - https://223.5.5.5/dns-query
    - https://1.1.1.1/dns-query

proxies:
  - name: "%s:443"
    type: vless
    server: "%s"
    port: 443
    uuid: %s
    udp: true
    tls: true
    client-fingerprint: chrome
    servername: "%s"
    network: ws
    ws-opts:
      path: "/?ed=2048"
      headers:
        Host: "%s"
""" % (DOMAIN, DOMAIN, UUID, DOMAIN, DOMAIN)


def fetch_vpngate():
    req = urllib.request.Request(VPNGATE_API, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def parse_ovpn(b64):
    """解析 base64 的 OpenVPN 配置，返回 dict。"""
    try:
        text = base64.b64decode(b64).decode("utf-8", errors="ignore")
    except Exception:
        return None
    info = {}
    m = re.search(r"^remote\s+(\S+)\s+(\d+)", text, re.M)
    if not m:
        return None
    info["server"] = m.group(1)
    info["port"] = int(m.group(2))
    m = re.search(r"^proto\s+(\S+)", text, re.M)
    info["proto"] = m.group(1).lower() if m else "tcp"
    # 只取 TCP 的，mihomo openvpn 走 tcp 更稳
    if "udp" in info["proto"]:
        # 尝试找 tcp 的 remote
        m2 = re.search(r"^remote\s+(\S+)\s+(\d+).*tcp", text, re.M | re.I)
        # 简单起见：udp 的跳过
        return None
    m = re.search(r"^cipher\s+(\S+)", text, re.M)
    info["cipher"] = m.group(1) if m else "AES-128-CBC"
    m = re.search(r"^auth\s+(\S+)", text, re.M)
    info["auth"] = m.group(1) if m else "SHA1"
    m = re.search(r"<ca>(.*?)</ca>", text, re.S)
    if not m:
        return None
    info["ca"] = m.group(1).strip()
    return info


def main():
    print("Fetching VPN Gate...", file=sys.stderr)
    data = fetch_vpngate()
    lines = data.strip().split("\n")
    # VPN Gate API 格式：第一行是 #HostName,IP,... 表头（# 开头但不是注释），
    # 最后一行是 *。不能简单过滤掉所有 # 开头行，否则会把表头也删掉。
    rows = []
    for l in lines:
        if l.startswith("*"):
            continue
        if l.startswith("#"):
            l = l.lstrip("#")  # 表头行：去掉开头的 #
        if l.strip():
            rows.append(l)
    reader = csv.reader(rows)
    header = next(reader)
    idx = {name: i for i, name in enumerate(header)}

    nodes = []
    country_count = {}
    for row in reader:
        try:
            if len(row) < len(header):
                continue
            country = row[idx["CountryShort"]]
            ip = row[idx["IP"]]
            b64 = row[idx["OpenVPN_ConfigData_Base64"]]
            if not b64:
                continue
            ovpn = parse_ovpn(b64)
            if not ovpn:
                continue
            # 按国家计数命名
            country_count[country] = country_count.get(country, 0) + 1
            name = "🏠 %s-家宽-%02d" % (country, country_count[country])
            nodes.append((name, ovpn))
            if len(nodes) >= MAX_NODES:
                break
        except Exception as e:
            print("skip row: %s" % e, file=sys.stderr)
            continue

    print("Got %d nodes" % len(nodes), file=sys.stderr)

    out = [HEADER]
    for name, ovpn in nodes:
        # ca 用 YAML block scalar，缩进处理
        ca_lines = "\n".join("      " + l for l in ovpn["ca"].split("\n"))
        out.append('''  - name: "%s"
    type: openvpn
    server: %s
    port: %d
    proto: tcp
    username: vpn
    password: vpn
    cipher: %s
    auth: %s
    udp: false
    handshake-timeout: 30
    remote-dns-resolve: true
    dns: [ 8.8.8.8, 1.1.1.1 ]
    dialer-proxy: "⚡ CF前置"
    ca: &jkca |-
%s
''' % (name, ovpn["server"], ovpn["port"], ovpn["cipher"], ovpn["auth"], ca_lines))

    # proxy-groups
    out.append('proxy-groups:\n')
    out.append('''  - name: "⚡ CF前置"
    type: url-test
    url: https://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
    proxies:
      - "%s:443"
''' % DOMAIN)
    out.append('''  - name: "🏠 家宽自动"
    type: fallback
    url: https://www.gstatic.com/generate_204
    interval: 1800
    lazy: true
    proxies:
''')
    for name, _ in nodes:
        out.append('      - "%s"\n' % name)

    # rules - 详细分流
    out.append('''rules:
  # ===== AI 类 -> 家宽 =====
  - DOMAIN-SUFFIX,openai.com,🏠 家宽自动
  - DOMAIN-SUFFIX,chatgpt.com,🏠 家宽自动
  - DOMAIN-SUFFIX,anthropic.com,🏠 家宽自动
  - DOMAIN-SUFFIX,claude.ai,🏠 家宽自动
  - DOMAIN-SUFFIX,google.ai,🏠 家宽自动
  - DOMAIN-SUFFIX,gemini.google.com,🏠 家宽自动
  - DOMAIN-SUFFIX,x.ai,🏠 家宽自动
  - DOMAIN-SUFFIX,grok.com,🏠 家宽自动
  - DOMAIN-SUFFIX,perplexity.ai,🏠 家宽自动
  - DOMAIN-SUFFIX,midjourney.com,🏠 家宽自动
  - DOMAIN-SUFFIX,cohere.com,🏠 家宽自动
  - DOMAIN-SUFFIX,mistral.ai,🏠 家宽自动
  - DOMAIN-SUFFIX,poe.com,🏠 家宽自动
  - DOMAIN-SUFFIX,character.ai,🏠 家宽自动
  - DOMAIN-SUFFIX,stability.ai,🏠 家宽自动
  - DOMAIN-SUFFIX,runway.ml,🏠 家宽自动
  - DOMAIN-SUFFIX,huggingface.co,🏠 家宽自动
  - DOMAIN-SUFFIX,copilot.microsoft.com,🏠 家宽自动
  - DOMAIN-SUFFIX,deepseek.com,🏠 家宽自动
  - DOMAIN-SUFFIX,kimi.moonshot.cn,🏠 家宽自动
  - DOMAIN-KEYWORD,copilot,🏠 家宽自动
  - DOMAIN-KEYWORD,chatgpt,🏠 家宽自动
  - DOMAIN-KEYWORD,claude,🏠 家宽自动

  # ===== Web3 / 加密 -> 家宽 =====
  - DOMAIN-SUFFIX,binance.com,🏠 家宽自动
  - DOMAIN-SUFFIX,coinbase.com,🏠 家宽自动
  - DOMAIN-SUFFIX,kraken.com,🏠 家宽自动
  - DOMAIN-SUFFIX,okx.com,🏠 家宽自动
  - DOMAIN-SUFFIX,bybit.com,🏠 家宽自动
  - DOMAIN-SUFFIX,uniswap.org,🏠 家宽自动
  - DOMAIN-SUFFIX,opensea.io,🏠 家宽自动
  - DOMAIN-SUFFIX,metamask.io,🏠 家宽自动
  - DOMAIN-SUFFIX,etherscan.io,🏠 家宽自动
  - DOMAIN-SUFFIX,coingecko.com,🏠 家宽自动
  - DOMAIN-SUFFIX,coinmarketcap.com,🏠 家宽自动
  - DOMAIN-SUFFIX,tradingview.com,🏠 家宽自动
  - DOMAIN-KEYWORD,binance,🏠 家宽自动
  - DOMAIN-KEYWORD,coinbase,🏠 家宽自动
  - DOMAIN-KEYWORD,web3,🏠 家宽自动
  - DOMAIN-KEYWORD,crypto,🏠 家宽自动
  - DOMAIN-KEYWORD,nft,🏠 家宽自动
  - DOMAIN-KEYWORD,defi,🏠 家宽自动

  # ===== 国外流媒体 -> 家宽 =====
  - DOMAIN-SUFFIX,netflix.com,🏠 家宽自动
  - DOMAIN-SUFFIX,netflix.net,🏠 家宽自动
  - DOMAIN-SUFFIX,youtube.com,🏠 家宽自动
  - DOMAIN-SUFFIX,youtu.be,🏠 家宽自动
  - DOMAIN-SUFFIX,googlevideo.com,🏠 家宽自动
  - DOMAIN-SUFFIX,disneyplus.com,🏠 家宽自动
  - DOMAIN-SUFFIX,disney-plus.net,🏠 家宽自动
  - DOMAIN-SUFFIX,hulu.com,🏠 家宽自动
  - DOMAIN-SUFFIX,hbomax.com,🏠 家宽自动
  - DOMAIN-SUFFIX,max.com,🏠 家宽自动
  - DOMAIN-SUFFIX,primevideo.com,🏠 家宽自动
  - DOMAIN-SUFFIX,amazonvideo.com,🏠 家宽自动
  - DOMAIN-SUFFIX,twitch.tv,🏠 家宽自动
  - DOMAIN-SUFFIX,spotify.com,🏠 家宽自动
  - DOMAIN-SUFFIX,scdn.co,🏠 家宽自动
  - DOMAIN-SUFFIX,apple.com,🏠 家宽自动
  - DOMAIN-SUFFIX,apple-cloudkit.com,🏠 家宽自动
  - DOMAIN-SUFFIX,itunes.com,🏠 家宽自动
  - DOMAIN-SUFFIX,mzstatic.com,🏠 家宽自动
  - DOMAIN-SUFFIX,tidal.com,🏠 家宽自动
  - DOMAIN-SUFFIX,pandora.com,🏠 家宽自动
  - DOMAIN-SUFFIX,soundcloud.com,🏠 家宽自动
  - DOMAIN-SUFFIX,vimeo.com,🏠 家宽自动
  - DOMAIN-SUFFIX,dailymotion.com,🏠 家宽自动
  - DOMAIN-SUFFIX,paramountplus.com,🏠 家宽自动
  - DOMAIN-SUFFIX,peacocktv.com,🏠 家宽自动
  - DOMAIN-SUFFIX,crunchyroll.com,🏠 家宽自动
  - DOMAIN-SUFFIX,funimation.com,🏠 家宽自动

  # ===== 国外社交 / 资讯 -> 家宽 =====
  - DOMAIN-SUFFIX,twitter.com,🏠 家宽自动
  - DOMAIN-SUFFIX,x.com,🏠 家宽自动
  - DOMAIN-SUFFIX,twimg.com,🏠 家宽自动
  - DOMAIN-SUFFIX,facebook.com,🏠 家宽自动
  - DOMAIN-SUFFIX,fbcdn.net,🏠 家宽自动
  - DOMAIN-SUFFIX,instagram.com,🏠 家宽自动
  - DOMAIN-SUFFIX,cdninstagram.com,🏠 家宽自动
  - DOMAIN-SUFFIX,threads.net,🏠 家宽自动
  - DOMAIN-SUFFIX,telegram.org,🏠 家宽自动
  - DOMAIN-SUFFIX,t.me,🏠 家宽自动
  - DOMAIN-SUFFIX,whatsapp.com,🏠 家宽自动
  - DOMAIN-SUFFIX,whatsapp.net,🏠 家宽自动
  - DOMAIN-SUFFIX,discord.com,🏠 家宽自动
  - DOMAIN-SUFFIX,discord.gg,🏠 家宽自动
  - DOMAIN-SUFFIX,reddit.com,🏠 家宽自动
  - DOMAIN-SUFFIX,redd.it,🏠 家宽自动
  - DOMAIN-SUFFIX,medium.com,🏠 家宽自动
  - DOMAIN-SUFFIX,substack.com,🏠 家宽自动
  - DOMAIN-SUFFIX,github.com,🏠 家宽自动
  - DOMAIN-SUFFIX,githubusercontent.com,🏠 家宽自动
  - DOMAIN-SUFFIX,stackoverflow.com,🏠 家宽自动
  - DOMAIN-SUFFIX,wikipedia.org,🏠 家宽自动
  - DOMAIN-SUFFIX,google.com,🏠 家宽自动
  - DOMAIN-SUFFIX,gstatic.com,🏠 家宽自动
  - DOMAIN-SUFFIX,gmail.com,🏠 家宽自动

  # ===== 国内 App / 网站 -> 直连 =====
  - DOMAIN-SUFFIX,qq.com,DIRECT
  - DOMAIN-SUFFIX,weixin.qq.com,DIRECT
  - DOMAIN-SUFFIX,wechat.com,DIRECT
  - DOMAIN-SUFFIX,alipay.com,DIRECT
  - DOMAIN-SUFFIX,alipayobjects.com,DIRECT
  - DOMAIN-SUFFIX,taobao.com,DIRECT
  - DOMAIN-SUFFIX,tmall.com,DIRECT
  - DOMAIN-SUFFIX,jd.com,DIRECT
  - DOMAIN-SUFFIX,jdcdn.com,DIRECT
  - DOMAIN-SUFFIX,360buyimg.com,DIRECT
  - DOMAIN-SUFFIX,pinduoduo.com,DIRECT
  - DOMAIN-SUFFIX,meituan.com,DIRECT
  - DOMAIN-SUFFIX,meituan.net,DIRECT
  - DOMAIN-SUFFIX,dianping.com,DIRECT
  - DOMAIN-SUFFIX,ele.me,DIRECT
  - DOMAIN-SUFFIX,amap.com,DIRECT
  - DOMAIN-SUFFIX,autonavi.com,DIRECT
  - DOMAIN-SUFFIX,baidu.com,DIRECT
  - DOMAIN-SUFFIX,bdstatic.com,DIRECT
  - DOMAIN-SUFFIX,bilibili.com,DIRECT
  - DOMAIN-SUFFIX,biliapi.com,DIRECT
  - DOMAIN-SUFFIX,biliapi.net,DIRECT
  - DOMAIN-SUFFIX,hdslb.com,DIRECT
  - DOMAIN-SUFFIX,douyin.com,DIRECT
  - DOMAIN-SUFFIX,iesdouyin.com,DIRECT
  - DOMAIN-SUFFIX,bytedance.com,DIRECT
  - DOMAIN-SUFFIX,toutiao.com,DIRECT
  - DOMAIN-SUFFIX,ixigua.com,DIRECT
  - DOMAIN-SUFFIX,kuaishou.com,DIRECT
  - DOMAIN-SUFFIX,kwai.com,DIRECT
  - DOMAIN-SUFFIX,weibo.com,DIRECT
  - DOMAIN-SUFFIX,sina.com.cn,DIRECT
  - DOMAIN-SUFFIX,sinajs.cn,DIRECT
  - DOMAIN-SUFFIX,zhihu.com,DIRECT
  - DOMAIN-SUFFIX,zhimg.com,DIRECT
  - DOMAIN-SUFFIX,douban.com,DIRECT
  - DOMAIN-SUFFIX,youku.com,DIRECT
  - DOMAIN-SUFFIX,iqiyi.com,DIRECT
  - DOMAIN-SUFFIX,qiyi.com,DIRECT
  - DOMAIN-SUFFIX,tencent.com,DIRECT
  - DOMAIN-SUFFIX,tencent-cloud.com,DIRECT
  - DOMAIN-SUFFIX,alicdn.com,DIRECT
  - DOMAIN-SUFFIX,aliyun.com,DIRECT
  - DOMAIN-SUFFIX,aliyuncs.com,DIRECT
  - DOMAIN-SUFFIX,csdn.net,DIRECT
  - DOMAIN-SUFFIX,cnblogs.com,DIRECT
  - DOMAIN-SUFFIX,juejin.cn,DIRECT
  - DOMAIN-SUFFIX,51cto.com,DIRECT
  - DOMAIN-SUFFIX,oschina.net,DIRECT
  - DOMAIN-SUFFIX,gitee.com,DIRECT
  - DOMAIN-SUFFIX,12306.cn,DIRECT
  - DOMAIN-SUFFIX,10086.cn,DIRECT
  - DOMAIN-SUFFIX,10010.com,DIRECT
  - DOMAIN-SUFFIX,189.cn,DIRECT
  - DOMAIN-SUFFIX,cmbchina.com,DIRECT
  - DOMAIN-SUFFIX,icbc.com.cn,DIRECT
  - DOMAIN-SUFFIX,ccb.com,DIRECT
  - DOMAIN-SUFFIX,boc.cn,DIRECT
  - DOMAIN-SUFFIX,abchina.com,DIRECT
  - DOMAIN-KEYWORD,wechat,DIRECT
  - DOMAIN-KEYWORD,weixin,DIRECT
  - DOMAIN-KEYWORD,alipay,DIRECT
  - DOMAIN-KEYWORD,taobao,DIRECT
  - DOMAIN-KEYWORD,jd.com,DIRECT
  # 国内 IP 直连
  - GEOIP,CN,DIRECT

  # ===== 兜底 =====
  - MATCH,🏠 家宽自动
''')

    sys.stdout.write("".join(out))


if __name__ == "__main__":
    main()
