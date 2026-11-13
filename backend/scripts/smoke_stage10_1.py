"""保留74项真实HTTP和缓存回放，追加选品类接口验收；上游通道禁网。"""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.scripts import smoke_stage7 as baseline, smoke_stage10 as stage10


def run_http(folder,port,password):
    result=stage10.run_http(folder,port,password)
    import httpx
    checks=result["checks"]
    def expect(name,response,status):
        checks.append({"check":name,"status":response.status_code,"expected":status})
        assert response.status_code==status,name
        return response.json()
    with httpx.Client(base_url=f"http://127.0.0.1:{port}",trust_env=False,timeout=30) as admin,httpx.Client(base_url=f"http://127.0.0.1:{port}",trust_env=False) as ordinary:
        expect("selected_fetch_admin_login",admin.post("/api/auth/login",json={"username":"stage7_admin","password":password}),200)
        expect("selected_fetch_user_login",ordinary.post("/api/auth/login",json={"username":"stage7_user","password":password}),200)
        expect("selected_fetch_ordinary_forbidden",ordinary.post("/api/fetch/run",json={"codes":["pc"]}),403)
        for name,codes in [("empty",[]),("duplicate",["pc","pc"]),("unknown",["unknown"])]:
            expect("selected_fetch_"+name,admin.post("/api/fetch/run",json={"codes":codes}),422)
        expect("selected_fetch_cannot_set_auto_slot",admin.post("/api/fetch/run",json={"codes":["pc"],"slot":"16:30"}),422)
        output=expect("selected_fetch_admin_scope",admin.post("/api/fetch/run",json={"codes":["pc"]}),200)
        assert output["summary"]["codes"]==["pc"] and len(output["commodity_results"])==1
        # 抓价禁网应如实失败；不把未抓到数据当成成功，且没有其他品类被执行。
        assert output["commodity_results"][0]["code"]=="pc" and output["commodity_results"][0]["outcome"]=="failed"
        runs=expect("selected_fetch_persisted_scope",admin.get("/api/admin/fetch-runs"),200)
        assert runs["items"][0]["summary"]["codes"]==["pc"]
    result["checks_passed"]=len(checks)
    result["stage10_1_selected_fetch_verified"]=True
    return result


if __name__=="__main__":
    baseline.run_http=run_http
    baseline.main()
