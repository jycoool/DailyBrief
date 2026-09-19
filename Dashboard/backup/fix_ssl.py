#!/usr/bin/env python3
"""
fix_ssl.py — 修復 Windows 上的 SSL 憑證驗證問題

症狀:
    curl: (60) SSL certificate ... unable to get local issuer certificate (20)

原因:
    yfinance 1.5+ 改用 curl_cffi，它預設只信任 certifi 內建的憑證庫。
    如果你的網路有公司代理伺服器、VPN 或防毒軟體在做 HTTPS 檢查，
    連線會被它們的憑證重新簽發，而 certifi 不認得那些憑證。

解法:
    把 certifi 的憑證庫 + Windows 系統憑證庫（含公司/防毒的根憑證）
    合併成一個 ca_bundle.pem，讓 curl 兩邊都信任。

用法:
    pip install wincertstore
    python fix_ssl.py

    完成後會在同目錄產生 ca_bundle.pem，
    market_monitor.py 會自動偵測並使用它。
"""

import os
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ca_bundle.pem")


def main():
    parts = []

    # 1. certifi 內建憑證庫
    try:
        import certifi
        with open(certifi.where(), "r", encoding="utf-8") as f:
            parts.append(f.read())
        print(f"[OK] 已載入 certifi 憑證庫: {certifi.where()}")
    except Exception as e:
        print(f"[!] 無法載入 certifi: {e}")

    # 2. Windows 系統憑證庫（公司代理／防毒的根憑證會在這裡）
    if os.name == "nt":
        try:
            import wincertstore
        except ImportError:
            sys.exit(
                "\n[X] 缺少 wincertstore 套件。請先執行:\n"
                "    pip install wincertstore\n"
                "    然後重新執行 python fix_ssl.py"
            )

        count = 0
        for storename in ("CA", "ROOT"):
            try:
                with wincertstore.CertSystemStore(storename) as store:
                    for cert in store.itercerts(usage=wincertstore.SERVER_AUTH):
                        pem = cert.get_pem()
                        if isinstance(pem, bytes):
                            pem = pem.decode("ascii", errors="ignore")
                        parts.append(pem)
                        count += 1
            except Exception as e:
                print(f"[!] 讀取 Windows 憑證庫 {storename} 失敗: {e}")
        print(f"[OK] 已從 Windows 憑證庫匯出 {count} 張憑證")
    else:
        print("[i] 非 Windows 系統，僅使用 certifi")

    if not parts:
        sys.exit("[X] 沒有取得任何憑證，無法產生 bundle")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    print(f"\n[完成] 已產生: {OUT}")
    print("\n接下來直接執行即可，market_monitor.py 會自動使用這個檔案:")
    print("    python market_monitor.py --fast")
    print("\n若要讓其他程式也使用，可設定環境變數:")
    print(f'    set CURL_CA_BUNDLE={OUT}')
    print(f'    set SSL_CERT_FILE={OUT}')

    # 順手測試一下
    print("\n正在測試連線...")
    os.environ["CURL_CA_BUNDLE"] = OUT
    os.environ["SSL_CERT_FILE"] = OUT
    os.environ["REQUESTS_CA_BUNDLE"] = OUT
    try:
        import yfinance as yf
        df = yf.download("AAPL", period="5d", progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            print(f"[成功] 抓到 AAPL {len(df)} 筆資料，最新收盤 "
                  f"{float(df['Close'].iloc[-1]):.2f}")
        else:
            print("[!] 連線成功但沒有資料，可能是 Yahoo 端的問題")
    except Exception as e:
        print(f"[X] 測試仍然失敗: {e}")
        print("\n若仍是憑證錯誤，請看下方「進階排查」。")


if __name__ == "__main__":
    main()
