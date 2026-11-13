"""沿用57项隔离HTTP与真实历史回放，追加个人口径的真实socket验收。"""
from pathlib import Path
import sys
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.scripts import smoke_stage7 as baseline

original_http = baseline.run_http


def run_http(folder, port, password):
    result = original_http(folder, port, password)
    import httpx
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.quotes import QuoteRow, store_row
    from backend.app.adapters.common import now
    engine = create_engine(f"sqlite:///{folder / 'data/procurement.sqlite3'}")
    # 数值只用于临时库的隔离对抗测试；不请求任何上游。
    with sessionmaker(engine, expire_on_commit=False)() as session:
        ids=[]
        for market,price in [("隔离市场A","100"),("隔离市场B","200")]:
            entry,_=store_row(session,QuoteRow(code="paper", product_name="隔离测试纸", specification="测试规格",
                market=market,price_type="测试价格",price=Decimal(price),quote_date=now().date(),source="free_paper",
                source_name="隔离fixture",source_url="https://example.invalid/quote",fetched_at=now().replace(tzinfo=None)))
            ids.append(entry.id)
        session.commit()
    engine.dispose()
    checks=result["checks"]
    def expect(name,response,status):
        checks.append({"check":name,"status":response.status_code,"expected":status})
        assert response.status_code==status,name
        return response.json()
    base=f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=base,trust_env=False) as a,httpx.Client(base_url=base,trust_env=False) as b,httpx.Client(base_url=base,trust_env=False) as guest:
        expect("quote_guest_rejected",guest.get("/api/quotes?code=paper"),401)
        for client,name in [(a,"stage7_user"),(b,"stage7_other")]:
            expect(name+"_quote_login",client.post("/api/auth/login",json={"username":name,"password":password}),200)
        for client,entry,name in [(a,ids[0],"A"),(b,ids[1],"B")]:
            expect(name+"_choose",client.put("/api/me/quote-selections/paper",json={"entry_id":entry}),200)
            options=expect(name+"_quote_options",client.get("/api/quotes?code=paper"),200)
            assert options["selected_entry_id"]==entry and len(options["items"])==2
            prices=expect(name+"_selected_latest",client.get("/api/prices/latest"),200)
            chosen=next(r for r in prices["items"] if r["code"]=="paper")
            assert chosen["selected_entry_id"]==entry
            history=expect(name+"_selected_history",client.get("/api/prices/history?code=paper"),200)
            assert len(history["items"])==1 and history["items"][0]["entry_id"]==entry
            assert expect(name+"_stable_default",client.get("/api/quotes?code=paper"),200)["selected_entry_id"]==entry
        expect("owner_override_rejected",a.put("/api/me/quote-selections/paper",json={"entry_id":ids[1],"user_id":3}),422)
        expect("cross_commodity_entry_rejected",a.put("/api/me/quote-selections/abs",json={"entry_id":ids[0]}),404)
        expect("unknown_entry_rejected",a.put("/api/me/quote-selections/paper",json={"entry_id":999999}),404)
        assert expect("B_selection_unchanged",b.get("/api/quotes?code=paper"),200)["selected_entry_id"]==ids[1]
    result["checks_passed"]=len(checks)
    result["stage10_personal_quote_isolation"]=True
    return result


if __name__ == "__main__":
    baseline.run_http=run_http
    baseline.main()
