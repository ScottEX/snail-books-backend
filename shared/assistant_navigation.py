"""App navigation skill and server-side route catalog for chat."""

from shared.app_search import keyword_variants

# IDs are the iOS AppSearchPage values handled by RootStack.
DESTINATIONS = {
    'profile': '个人中心、个人资料、个人信息、个人、账户资料、profile、my account、個人中心、個人資料',
    'account': '账户信息、昵称、账号资料、account info、帳戶資訊、账号、帳號、暱稱',
    'email': '修改邮箱、更换邮箱、change email、修改郵箱、更改邮箱、更改郵箱、email',
    'password': '修改密码、改密码、更换密码、change password、改密碼、更改密码、更改密碼、更換密碼、password',
    'security': '安全设置、账户安全、security settings、安全設定、帳戶安全、account security',
    'avatar': '更换头像、换头像、change avatar、換頭像、修改头像、修改頭像、avatar',
    'language': '语言设置、切换语言、language settings、語言設定、切換語言、偏好设置、偏好設定、language、preferences',
    'themeSettings': '主题设置、背景设置、背景图、theme settings、背景圖、背景透明度、背景設定、主題設定、background',
    'userManagement': '用户管理、使用者管理、审核用户、manage users、用戶管理、管理用户、管理用戶、管理使用者、審核用戶、编辑用户、編輯用戶、user management、review users',
    'invoice': '发票、发票管理、invoice、發票、開票、开票、票據',
    'invoiceInfo': '发票资料、开票资料、billing details、發票資料、开票信息、開票資訊、invoice info',
    'invoiceRecords': '发票记录、开票记录、invoice records、發票紀錄、開票記錄、发票历史、發票歷史、上传发票、上傳發票、upload invoice',
    'invoiceApply': '申请开票、apply for invoice、申請開票、新建发票、新增發票、new invoice',
    'expense': '支出记录、支出历史、expense history、支出記錄、支出歷史、流水',
    'expenseEntry': '录入支出、凭证上传、记账表单、add expense、记账、記帳、輸入支出、憑證上傳',
    'procurement': '采购管理、进货管理、procurement、採購管理、進貨管理',
    'procurementHistory': '采购记录、进货记录、purchase history、採購記錄、采购历史、採購歷史、進貨記錄、清账、清賬、采购结算、採購結算、采购单PDF、採購單PDF、settlement',
    'newOrder': '新建采购单、录入采购、new purchase order、采购、採購、进货、進貨、新增採購單',
    'products': '商品管理、产品管理、products、商品、产品、產品、產品管理、编辑商品、編輯商品、编辑产品、編輯產品、供应商、供應商、edit product、supplier',
    'procurementCart': '购物车、采购车、cart、購物車、採購車、采购清单、採購清單',
    'statistics': '经营总览、收支统计、统计图表、statistics、统计、統計、图表、圖表、經營總覽、收支总览、收支總覽、收支統計、charts、trends',
    'revenue': '营收记录、营业额明细、revenue history、營收記錄、营收历史、營收歷史',
    'revenueEntry': '录入营收、登记营业额、daily revenue、营收、營收、营业额、營業額、輸入營收',
    'reconciliation': '对账记录、对账历史、reconciliation history、對帳記錄、對賬記錄、對帳歷史、recon history',
    'reconciliationEntry': '录入对账、登记对账、reconciliation、对账、對帳、對賬、日记账、日記帳',
    'fee': '平台手续费、platform fees、平台手續費、手续费、手續費',
    'feeHistory': '手续费记录、手续费历史、fee history、手續費歷史、手續費記錄',
    'feeEntry': '录入手续费、add platform fee、錄入手續費、輸入手續費、添加手续费、新增手續費',
    'partnerStructure': '股东、持股比例、股权结构、shareholders、合伙人结构、合夥人結構、股權結構、组织架构、組織架構、partner structure',
    'dividendHistory': '分红记录、分红历史、dividend history、分紅記錄、分紅歷史',
    'refund': '退款、退费、退費、refund',
    'expenseAi': '一句话记账、一句話記帳、智能记账、智慧記帳、ai expense',
    'addProduct': '添加商品、新增商品、添加产品、新增產品、add product、new product',
    'partner': '合伙人、合夥人、股东、股東、partners',
    'issueDividend': '发起分红、發起分紅、分红、分紅、issue dividend',
    'cover': '更换封面、更換封面、换封面、換封面、cover photo',
    'signature': '签名、簽名、个性签名、個性簽名、signature',
    'theme': '主题、主題、切换主题、切換主題、theme',
    'loginSecurity': '登录安全、登入安全、登录设置、登入設定、sign-in security',
    'faceId': '面容登录、面容登入、指纹登录、指紋登入、face id、touch id、biometric login',
    'singleSession': '单设备登录、單裝置登入、单会话、單會話、single session',
    'sessionTimeout': '会话超时、工作階段逾時、自动登出、自動登出、session timeout',
    'deleteAccount': '注销账户、註銷帳戶、删除账号、刪除帳號、delete account',
    'logout': '退出登录、登出、注销登录、log out、sign out',
}

NAVIGATION_SKILL = """
Use find_app_destinations when the user wants to go to an app page, asks where a feature
is, or asks how to find it. For 我要查看个人信息 or 我想看看我的账户资料 use profile.
When asking for actual ledger data, use a query tool. If a useful destination is clear,
also return its ID with a navigation call. Ask one question if navigation is ambiguous.
Use only catalog IDs, never produce a URL or claim the app has already navigated.
The app shows a button; the user chooses whether to open it.
"""

_AMBIGUOUS_METRICS = {'营业额', '营收', '分红', '股东', 'shareholders'}
_HISTORY_KEYWORDS = {'历史', '历史记录', '历史纪录', '记录', '纪录', 'history', 'records'}
_HISTORY_DESTINATIONS = (
    'expense', 'revenue', 'procurementHistory', 'reconciliation',
    'invoiceRecords', 'feeHistory', 'dividendHistory',
)


def find_app_destinations(route, *, is_admin):
    if not isinstance(route, str) or route not in DESTINATIONS:
        return None
    return route if is_admin or route != 'userManagement' else None


def match_destination(text, *, is_admin):
    """Local fallback for navigating by an exact or clearly requested feature."""
    variants = keyword_variants(text.casefold().strip('？?。！! '))
    navigation = ('查看', '打开', '在哪', '怎么', '带我去', '我想看', 'open ', 'where ', 'show me ')
    matches = [
        (len(alias), route)
        for route, aliases in DESTINATIONS.items()
        for alias in aliases.split('、')
        if any(variant == alias or (alias in variant and any(word in variant for word in navigation))
               for variant in variants)
    ]
    if not matches:
        return None
    return find_app_destinations(max(matches)[1], is_admin=is_admin)


def exact_destinations(text):
    variants = set(keyword_variants(text.casefold().strip('？?。！! ')))
    if variants.intersection(_HISTORY_KEYWORDS):
        return list(_HISTORY_DESTINATIONS)
    if variants.intersection(_AMBIGUOUS_METRICS):
        return []
    return [route for route, aliases in DESTINATIONS.items() if variants.intersection(aliases.split('、'))]
