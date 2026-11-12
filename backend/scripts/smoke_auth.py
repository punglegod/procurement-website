"""旧阶段验收脚本的交互式登录；密码不放命令行、环境或日志。"""

from getpass import getpass


def add_auth_arguments(parser):
    parser.add_argument("--username", help="验收账号，省略时交互输入；需管理员以验收来源状态")


def login(client, args):
    username = args.username or input("验收管理员账号：")
    password = getpass("验收账号密码：")
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    if response.status_code != 200:
        raise RuntimeError(f"验收登录失败（HTTP {response.status_code}）")
    if response.json()["role"] != "admin":
        raise RuntimeError("完整旧阶段验收需要管理员账号")
