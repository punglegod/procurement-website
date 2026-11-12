"""只读验收单服务托管与真实行情，导出页面组件验收所需的真实响应。"""

import argparse
import json
from pathlib import Path
import re

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=15, trust_env=False) as client:
        page = client.get("/")
        assert page.status_code == 200 and 'id="app"' in page.text
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
        assert assets, "未找到前端构建资源"
        root = Path(__file__).resolve().parents[2]
        assets = sorted(set(assets) | {f"/assets/{file.name}" for file in (root / "frontend/dist/assets").iterdir()
                                     if file.suffix in (".js", ".css")})
        for asset in assets:
            response = client.get(asset)
            assert response.status_code == 200, asset
            assert "javascript" in response.headers["content-type"] if asset.endswith(".js") else "text/css" in response.headers["content-type"]
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/missing").status_code == 404
        assert client.get("/assets/missing.js").status_code == 404
        latest = client.get("/api/prices/latest").json()
        assert len(latest["items"]) == 11
        aluminum = next(row for row in latest["items"] if row["code"] == "alu_a00")
        assert aluminum["price_point"]["price"] > 0
        assert aluminum["price_point"]["source"] == "free"
        history = {}
        for days in (7, 30, 90):
            response = client.get("/api/prices/history", params={"code": "alu_a00", "days": days})
            assert response.status_code == 200
            history[str(days)] = response.json()
            assert history[str(days)]["items"], "时间范围中无铝锭真实报价，请检查本地数据库及日期"
        states = client.get("/api/sources/status").json()
    output = {"base_url": args.base_url, "assets": assets, "latest": latest, "history": history, "sources": states}
    path = Path(__file__).resolve().parents[2] / "data/stage2-live.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: 首页及 {len(assets)} 个构建资源 HTTP 200；API 与缺失资源路由正常")
    point = aluminum["price_point"]
    print(f"PASS: 11 品种；铝锭 {point['price']} {point['unit']}，报价日 {point['quote_date']}")
    print(f"PASS: 7/30/90 天真实历史记录数：{[len(history[str(days)]['items']) for days in (7, 30, 90)]}")
    print(f"真实响应已导出至 {path.relative_to(path.parents[1])}；未触发抓取或修改价格库")


if __name__ == "__main__":
    main()
