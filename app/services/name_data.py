# -*- coding: utf-8 -*-
"""起名助手的本地字库字典。

全部为内置静态数据，不依赖网络。覆盖 7 大类：
人物 / 地点 / 势力 / 装备 / 功法 / 怪兽 / 其他（丹药·称号·灵物），
供 NameGeneratorDialog 随机组合生成名字。
"""
from __future__ import annotations

import random

# ----------------------------------------------------------------------
# 人物：中文
# ----------------------------------------------------------------------
CN_SURNAMES = [
    "李", "王", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴", "徐", "孙", "胡",
    "朱", "高", "林", "何", "郭", "马", "罗", "梁", "宋", "郑", "谢", "韩", "唐",
    "冯", "于", "董", "萧", "程", "曹", "袁", "邓", "许", "傅", "沈", "曾", "彭",
    "吕", "苏", "卢", "蒋", "蔡", "贾", "丁", "魏", "薛", "叶", "阎", "余", "潘",
    "杜", "戴", "夏", "钟", "汪", "田", "任", "姜", "范", "方", "石", "姚", "谭",
    "廖", "邹", "熊", "金", "陆", "郝", "孔", "白", "崔", "康", "毛", "邱", "秦",
    "江", "史", "顾", "侯", "邵", "孟", "龙", "万", "段", "雷", "钱", "汤", "尹",
    "易", "常", "武", "乔", "贺", "赖", "龚", "文", "慕容", "欧阳", "上官", "司徒",
    "南宫", "东方", "夜", "云", "苏", "洛", "楚", "君", "慕", "白", "叶", "凌",
]

CN_MALE_CHARS = [
    "伟", "强", "磊", "军", "洋", "勇", "杰", "涛", "超", "明", "刚", "平", "辉",
    "健", "俊", "帆", "昊", "天", "宇", "辰", "泽", "睿", "铭", "锦", "彦", "博",
    "翰", "弘", "毅", "承", "景", "澄", "骁", "凛", "绝", "渊", "珩", "砚", "聿",
    "叙", "竟", "既", "晏", "崇", "岳", "锋", "远", "征", "鸿", "鹏", "翔", "旭",
    "晨", "阳", "霖", "霄", "钦", "容", "桓", "湛", "澈", "渊", "叙", "驰", "勋",
]

CN_FEMALE_CHARS = [
    "芳", "娜", "敏", "静", "丽", "娟", "艳", "秀", "霞", "婷", "慧", "雪", "琳",
    "茜", "涵", "雨", "梦", "语", "汐", "蕊", "棠", "绾", "鸢", "黛", "窈", "婉",
    "柔", "姬", "娆", "怜", "初", "晚", "吟", "瑟", "绫", "瑶", "珊", "瑾", "璇",
    "菱", "芷", "兰", "竹", "菊", "梅", "樱", "葵", "茉", "莉", "菡", "莘", "莘",
]

CN_NEUTRAL_CHARS = [
    "安", "然", "之", "亦", "若", "清", "云", "月", "星", "辰", "风", "华", "卿",
    "言", "书", "白", "知", "行", "归", "尘", "暮", "晓", "寒", "温", "宁", "宸",
    "墨", "离", "笙", "歌", "阑", "烬", "烛", "鹤", "洲", "汀", "屿", "舟", "桥",
]

CN_MALE_WORDS = [
    "天佑", "子墨", "浩然", "子轩", "宇轩", "皓轩", "景行", "明煦", "思远", "志泽",
    "博文", "旭尧", "英杰", "熙诚", "明哲", "立诚", "天翊", "昊强", "鹤轩", "伟祺",
    "荣轩", "越泽", "浩宇", "瑾瑜", "皓宸", "嘉熙", "子骞", "漠然", "斩秋", "惊鸿",
]

CN_FEMALE_WORDS = [
    "雨薇", "诗涵", "梦琪", "语嫣", "紫萱", "若曦", "思彤", "静姝", "清瑶", "月华",
    "疏影", "暗香", "凝眸", "浅夏", "初雪", "挽歌", "念卿", "南烟", "青黛", "黛眉",
    "绾绾", "灼华", "桃夭", "采薇", "芷若", "玉如", "芸熙", "芷蕾", "含烟", "菱素",
]

# ----------------------------------------------------------------------
# 人物：西方（音译）
# ----------------------------------------------------------------------
WEST_MALE_FIRST = [
    "亚历山大", "艾伦", "安德鲁", "本杰明", "卡尔", "查尔斯", "克里斯", "丹尼尔",
    "大卫", "爱德华", "埃里克", "菲利克斯", "弗兰克", "加百列", "乔治", "哈里",
    "亨利", "艾萨克", "杰克", "詹姆斯", "杰森", "约翰", "约瑟夫", "朱利安",
    "凯撒", "凯文", "莱昂纳多", "路易", "卢卡斯", "马克", "马修", "迈克尔",
    "内森", "尼古拉斯", "奥利弗", "奥斯卡", "欧文", "帕特里克", "彼得", "菲利普",
    "雷蒙德", "理查德", "罗伯特", "罗兰", "塞缪尔", "斯科特", "肖恩", "西蒙",
    "斯特凡", "西奥多", "托马斯", "特拉维斯", "维克托", "文森特", "威廉", "扎克",
]

WEST_FEMALE_FIRST = [
    "艾丽丝", "阿曼达", "艾米", "安娜", "艾琳", "芭芭拉", "贝拉", "卡罗尔",
    "卡珊德拉", "凯瑟琳", "夏洛特", "克洛伊", "克莱尔", "黛西", "黛安娜", "艾拉",
    "艾琳娜", "艾米丽", "艾玛", "菲奥娜", "盖比", "吉赛尔", "格蕾丝", "汉娜",
    "海伦", "艾薇", "珍妮弗", "朱莉娅", "凯特", "莉莉", "琳达", "丽莎",
    "露西", "玛利亚", "梅根", "梅拉妮", "米娅", "娜塔莉", "妮可", "奥利维亚",
    "菲比", "蕾切尔", "丽贝卡", "罗莎", "露比", "萨曼莎", "索菲亚", "斯嘉丽",
    "蒂芙尼", "瓦莱丽", "维多利亚", "薇薇安", "温迪", "伊冯", "泽塔",
]

WEST_MALE_LAST = [
    "史密斯", "约翰逊", "威廉姆斯", "布朗", "琼斯", "米勒", "戴维斯", "威尔逊",
    "安德森", "泰勒", "摩尔", "杰克逊", "怀特", "哈里斯", "马丁", "汤普森",
    "加西亚", "克拉克", "刘易斯", "沃克", "霍尔", "艾伦", "杨", "金",
    "赖特", "斯科特", "格林", "贝克", "亚当斯", "纳尔逊", "卡特", "米切尔",
    "特纳", "菲利普斯", "坎贝尔", "帕克", "埃文斯", "爱德华兹", "柯林斯", "斯图尔特",
    "莫里森", "福斯特", "罗素", "巴恩斯", "亨德森", "科尔曼", "詹金斯", "佩里",
]

WEST_FEMALE_LAST = WEST_MALE_LAST

# ----------------------------------------------------------------------
# 人物：日式
# ----------------------------------------------------------------------
JP_MALE_FIRST = [
    "太郎", "次郎", "健一", "大辅", "翔太", "拓海", "健太", "直树", "悠斗", "莲",
    "阳翔", "苍", "湊", "树", "蓮", "悠真", "飒", "奏", "凪", "慧",
    "亮", "勇", "诚", "达也", "裕太", "圭吾", "凉介", "修", "司", "宏",
]

JP_FEMALE_FIRST = [
    "樱", "美咲", "由美", "惠", "爱", "结衣", "阳菜", "凛", "葵", "汐里",
    "美月", "千夏", "诗织", "绫乃", "铃", "澪", "雫", "七海", "花音", "真由",
    "香菜", "香织", "里奈", "奈奈", "优奈", "雏", "心爱", "步美", "舞", "茜",
]

JP_SURNAMES = [
    "佐藤", "铃木", "高桥", "田中", "渡边", "伊藤", "山本", "中村", "小林", "加藤",
    "吉田", "山田", "佐佐木", "山口", "松本", "井上", "木村", "林", "斋藤", "清水",
    "山崎", "森", "池田", "桥本", "阿部", "石川", "前田", "藤田", "后藤", "冈田",
    "长谷川", "村上", "近藤", "石井", "斋藤", "坂本", "远藤", "青木", "藤井", "西村",
    "福田", "太田", "三浦", "冈本", "松田", "中岛", "中原", "本田", "山下", "小川",
]

# ----------------------------------------------------------------------
# 地点
# ----------------------------------------------------------------------
CN_PLACES_PREFIX = [
    "青", "苍", "云", "临", "洛", "雁", "墨", "流", "长", "凌",
    "望", "听", "栖", "落", "枕", "镜", "寒", "暖", "旧", "新",
]
CN_PLACES_SUFFIX = [
    "州", "城", "山", "谷", "川", "溪", "湖", "海", "崖", "原",
    "镇", "关", "渡", "桥", "港", "崖", "岭", "坡", "泽", "湾",
]
CN_PLACES_FULL = [
    "长安", "洛阳", "金陵", "姑苏", "临安", "汴京", "凉州", "并州", "荆州", "益州",
    "幽州", "冀州", "青州", "扬州", "交州", "雍州", "云中", "雁门", "渔阳", "敦煌",
    "桃花坞", "落霞谷", "听雨楼", "栖凤台", "沉香浦", "白帝城", "黄鹤镇", "朱雀街",
]

WEST_PLACES = [
    "艾泽拉", "维斯特兰", "诺德港", "银月城", "黑石堡", "灰烬谷", "风语峡", "星坠湖",
    "晨曦镇", "暮色林", "霜风寒原", "雷霆崖", "白银滩", "灰鸦岭", "金穗平原", "静水河",
    "洛克福德", "阿维隆", "贝尔蒙特", "卡斯特顿", "德里菲尔德", "伊斯特伍德",
    "法尔茅斯", "格兰维尔", "哈罗盖特", "艾尔福德", "肯特米尔", "朗斯代尔",
]

JP_PLACES = [
    "月见町", "花守村", "雪野原", "风之谷", "星降里", "潮鸣滨", "霞关", "雾隐乡",
    "青叶台", "白砂滩", "千本木", "神乐坂", "落樱坡", "萤川", "汐见台", "朝雾町",
    "秋叶原", "藤见泽", "雨宫台", "椿山庄", "初音丘", "若叶台", "音羽桥", "深山驿",
    "光之丘", "水无月", "竹取泽", "常磐村",
]

# 地点后缀（西方 / 日式）
WEST_PLACE_SUFFIX = ["城", "港", "镇", "堡", "谷", "原", "湖", "滩"]
JP_PLACE_SUFFIX = ["町", "村", "坂", "原", "川", "滨", "台", "乡"]

# ----------------------------------------------------------------------
# 势力
# ----------------------------------------------------------------------
FACTION_RIGHT_PREFIX = [
    "青云", "天玄", "浩然", "紫霄", "太清", "凌云", "万剑", "御灵", "圣光",
    "明心", "清微", "昊天", "玉虚", "碧游", "白鹿", "正阳", "天枢", "归元",
    "太乙", "纯阳", "七星", "藏锋", "望月", "听雪", "流云", "栖霞", "凌霄",
    "紫阳", "丹霞", "鹤鸣",
]
FACTION_RIGHT_SUFFIX = ["宗", "门", "派", "阁", "盟", "山庄", "书院", "谷", "台", "峰"]
FACTION_EVIL_PREFIX = [
    "幽冥", "血煞", "修罗", "万毒", "噬魂", "魔音", "黑水", "蚀骨", "罗刹",
    "阴风", "赤炼", "幽泉", "鬼泣", "邪月", "蚀日", "暗影", "尸魔", "瘴云",
    "百蛊", "堕仙", "阎罗", "冥河", "鬼门", "血屠", "邪影", "断魂", "枯骨",
]
FACTION_EVIL_SUFFIX = ["教", "宗", "殿", "门", "谷", "渊", "窟", "寨", "坛", "狱"]
FACTION_NEUTRAL_PREFIX = [
    "千机", "听雨", "聚宝", "四海", "百晓", "拾遗", "醉月", "烟雨", "清风",
    "闲云", "墨韵", "百花", "万象", "通宝", "坐忘", "观澜", "醉仙", "藏珍",
    "尘外", "无名", "云来", "悦来", "望仙", "摘星", "揽月", "枕流", "漱玉",
]
FACTION_NEUTRAL_SUFFIX = ["阁", "楼", "斋", "坊", "会", "庄", "堂", "居", "轩", "馆"]

# ----------------------------------------------------------------------
# 装备
# ----------------------------------------------------------------------
EQUIP_WEAPON_PREFIX = [
    "诛仙", "青冥", "赤霄", "寒月", "破军", "惊鸿", "流云", "裂天", "凤鸣",
    "龙吟", "湛卢", "承影", "含光", "断水", "凝血", "烈焰", "玄冰", "紫电",
    "奔雷", "逐日", "追星", "落霞", "饮霜", "斩星", "裂空", "镇岳", "拂雪",
    "啸风", "断岳", "擒龙",
]
EQUIP_WEAPON_TYPES = [
    "剑", "刀", "枪", "戟", "弓", "鞭", "锏", "斧", "杖", "扇", "矛",
    "匕首", "锤", "软剑", "长枪", "神弓", "宝刀",
]
EQUIP_ARMOR_PREFIX = [
    "玄武", "龙鳞", "星辉", "玄龟", "紫金", "天蚕", "冰蚕", "墨鳞", "银霜",
    "赤焰", "玄铁", "寒玉", "凤羽", "乌金", "流光", "镇岳", "避水", "御火",
    "驱寒", "匿影",
]
EQUIP_ARMOR_TYPES = [
    "宝甲", "软甲", "法袍", "长衣", "重铠", "玉盾", "战盔", "云靴", "护腕",
    "护心镜", "披风", "战袍",
]
EQUIP_ACC_PREFIX = [
    "玲珑", "噬魂", "摄魂", "聚灵", "通灵", "幻月", "星辰", "碧水", "炎阳",
    "凝霜", "千幻", "镇魂", "引雷", "藏珍", "招福", "辟邪", "定神", "归元",
    "锁心", "避劫",
]
EQUIP_ACC_TYPES = [
    "玉佩", "戒指", "项链", "手镯", "耳坠", "发簪", "扳指", "令牌", "葫芦",
    "香囊", "玉镯", "灵珠",
]

# ----------------------------------------------------------------------
# 功法
# ----------------------------------------------------------------------
SKILL_SWORD_PREFIX = [
    "太虚", "万剑", "青云", "落英", "惊鸿", "无双", "裂空", "寒霜", "流光",
    "天罡", "紫霄", "断岳", "飞星", "凌霄", "惊雷", "摘星", "洗剑", "归藏",
    "焚岳", "引星",
]
SKILL_SWORD_TYPES = ["剑诀", "剑法", "剑意", "剑阵", "剑典", "剑气"]
SKILL_PALM_PREFIX = [
    "落英", "开山", "裂石", "排云", "翻天", "碎玉", "惊涛", "崩雷", "焚天",
    "寒冰", "覆海", "碎星", "大力", "千斤", "化骨", "追魂", "震岳", "拨云",
]
SKILL_PALM_TYPES = ["掌", "掌法", "神掌", "印", "掌诀"]
SKILL_MIND_PREFIX = [
    "纯阳", "玄冥", "太阴", "混元", "先天", "紫府", "无量", "清心", "洗髓",
    "易筋", "御灵", "归一", "坐忘", "抱朴", "养神", "天元", "观想", "守一",
]
SKILL_MIND_TYPES = ["心法", "真经", "诀", "神功", "功", "典"]
SKILL_MOVE_PREFIX = [
    "凌波", "踏雪", "追风", "幻影", "流云", "惊鸿", "缩地", "逐月", "御风",
    "无相", "残影", "掠影", "游龙", "穿云", "逐电", "隐雾",
]
SKILL_MOVE_TYPES = ["步", "身法", "影", "遁", "诀"]

# ----------------------------------------------------------------------
# 怪兽
# ----------------------------------------------------------------------
MON_YAO_MOD = [
    "三尾", "九尾", "六翼", "双头", "独角", "玄纹", "赤目", "青鳞", "金瞳",
    "墨羽", "碧眼", "白额", "千年", "百年", "通灵", "化形",
]
MON_YAO_ANIMAL = [
    "灵狐", "火狐", "雪狐", "妖狼", "黑豹", "紫貂", "血蟒", "蛟蛇", "鸦妖",
    "蛛后", "蜈祖", "鹿妖", "鹰妖", "鲛人", "猿妖", "鼠精", "狐妖", "虎妖",
]
MON_LING_MOD = ["雪", "玉", "银", "月", "云", "辉", "素", "丹", "彩", "金"]
MON_LING_CORE = [
    "麒麟", "白鹿", "仙鹤", "灵狐", "锦鲤", "白泽", "灵雀", "雪貂", "月兔",
    "青鸾", "丹凤", "玉龟",
]
MON_XIONG_MOD = ["太古", "上古", "荒古", "蛮荒", "灭世", "噬天", "吞山", "裂地", "焚海", "撼岳"]
MON_XIONG_CORE = [
    "饕餮", "穷奇", "梼杌", "混沌", "烛龙", "应龙", "九婴", "修蛇", "大风",
    "封豨", "玄武凶魂", "白骨凶猿",
]

# ----------------------------------------------------------------------
# 其他：丹药 / 称号 / 灵物
# ----------------------------------------------------------------------
PILL_PREFIX = [
    "聚气", "回春", "凝神", "筑基", "洗髓", "清心", "护脉", "疗伤", "避尘",
    "驱寒", "化瘀", "九转还魂", "大还", "小还", "玉髓", "龙虎", "紫金", "破障",
    "悟道", "凝婴", "培元", "生肌", "驻颜", "辟谷", "定魂", "续命",
]
PILL_TAIL = ["丹", "丸", "散"]
TITLE_REGION = [
    "北境", "南疆", "东海", "西漠", "中州", "九天", "万剑", "幽都", "雪原",
    "星海", "玄天", "赤炎", "沧澜", "镇岳", "孤山", "远洋",
]
TITLE_ROLE = [
    "剑仙", "刀皇", "枪王", "魔君", "药王", "琴圣", "棋绝", "书圣", "拳尊",
    "箭神", "商魁", "匠神", "医仙", "毒尊", "笔圣", "酒狂", "花魁", "阵师",
]
OTHER_ITEMS = [
    "天外陨铁", "九叶灵芝", "千年雪莲", "龙血草", "凤尾竹", "星辰砂",
    "月光石", "地心火莲", "玄冰髓", "紫金精", "雷击木", "碧水珠", "御风羽",
    "幽冥花", "仙人遗骨", "万年寒玉", "龙涎香", "凤凰翎", "麒麟血玉",
    "岁星石", "太岁土", "琥珀髓", "避水珠", "缚灵索", "引魂灯", "山河扇",
]


def _sample_unique(pool: list[str], count: int) -> list[str]:
    """从一个池子里随机抽取不重复元素；池子不足时允许全取。"""
    k = min(count, len(pool))
    return random.sample(pool, k)


def _cn_person(gender: str, surname: str, given: str) -> str:
    surnames = [surname] if surname else CN_SURNAMES
    if given:
        full_pool = [s + given for s in surnames]
        return random.choice(full_pool)

    if gender == "male":
        chars = CN_MALE_CHARS + CN_MALE_WORDS + CN_NEUTRAL_CHARS
    elif gender == "female":
        chars = CN_FEMALE_CHARS + CN_FEMALE_WORDS + CN_NEUTRAL_CHARS
    else:
        chars = (
            CN_MALE_CHARS + CN_FEMALE_CHARS + CN_NEUTRAL_CHARS
            + CN_MALE_WORDS + CN_FEMALE_WORDS
        )

    results: set[str] = set()
    surnames_pool = _sample_unique(surnames, 40)
    while len(results) < 21 and surnames_pool:
        s = random.choice(surnames_pool)
        if random.random() < 0.35:
            name = s + random.choice(chars) + random.choice(chars)
        else:
            name = s + random.choice(chars)
        results.add(name)
    return "" if not results else random.choice(sorted(results))


def _generate_person(style: str, gender: str, surname: str, given: str) -> str:
    """按风格生成单个人名。gender: male / female / any（作为人物页的子类型）。"""
    if style == "cn":
        return _cn_person(gender, surname, given)

    if style == "west":
        if gender == "female":
            first = random.choice(WEST_FEMALE_FIRST)
        elif gender == "male":
            first = random.choice(WEST_MALE_FIRST)
        else:
            first = random.choice(WEST_FEMALE_FIRST + WEST_MALE_FIRST)
        last = random.choice(WEST_MALE_LAST)
        if surname:
            last = surname
        if given:
            first = given
        return f"{first}·{last}"

    # 日式
    if gender == "female":
        first = random.choice(JP_FEMALE_FIRST)
    elif gender == "male":
        first = random.choice(JP_MALE_FIRST)
    else:
        first = random.choice(JP_FEMALE_FIRST + JP_MALE_FIRST)
    last = surname if surname else random.choice(JP_SURNAMES)
    if given:
        first = given
    return f"{last}{first}"


def _generate_place(style: str, surname: str, given: str) -> str:
    """按风格生成单个地名。style: cn / west / jp。"""
    if style == "cn":
        if given:
            return given + random.choice(CN_PLACES_SUFFIX)
        if surname:
            return surname + random.choice(CN_PLACES_SUFFIX)
        if random.random() < 0.5:
            return random.choice(CN_PLACES_FULL)
        return random.choice(CN_PLACES_PREFIX) + random.choice(CN_PLACES_SUFFIX)
    if style == "west":
        if given:
            return given + random.choice(WEST_PLACE_SUFFIX)
        if surname:
            if random.random() < 0.4:
                return surname + "兰"
            return surname + random.choice(WEST_PLACE_SUFFIX)
        return random.choice(WEST_PLACES)
    # 日式
    if given:
        return given + random.choice(JP_PLACE_SUFFIX)
    if surname:
        return surname + random.choice(JP_PLACE_SUFFIX)
    return random.choice(JP_PLACES)


def generate_names(
    kind: str,
    subtype: str,
    style: str = "cn",
    surname: str = "",
    given: str = "",
    suffix: str = "",
    count: int = 24,
) -> list[str]:
    """根据页签与子类型随机生成 count 个不重复的名称。

    kind:    person 人物 / place 地点 / faction 势力 / equip 装备 /
             skill 功法 / monster 怪兽 / other 其他
    subtype: 每类的子筛选（人物的性别、装备的武器防具饰品等）
    style:   人物页的风格 cn / west / jp
    suffix:  指定后缀（选填，直接拼在名称末尾）
    """
    suffix = suffix.strip()
    surname = surname.strip()
    given = given.strip()
    results: set[str] = set()

    def fill(producer) -> None:
        attempts = 0
        while len(results) < count and attempts < 900:
            attempts += 1
            name = producer()
            if name:
                results.add(name + suffix)

    if kind == "person":
        fill(lambda: _generate_person(style, subtype, surname, given))
    elif kind == "place":
        place_style = {"east": "cn", "west": "west", "jp": "jp"}.get(subtype, "cn")
        fill(lambda: _generate_place(place_style, surname, given))
    elif kind == "faction":
        pools = {
            "right": (FACTION_RIGHT_PREFIX, FACTION_RIGHT_SUFFIX),
            "evil": (FACTION_EVIL_PREFIX, FACTION_EVIL_SUFFIX),
            "neutral": (FACTION_NEUTRAL_PREFIX, FACTION_NEUTRAL_SUFFIX),
        }
        pre, suf = pools.get(subtype, pools["right"])
        fill(lambda: random.choice(pre) + random.choice(suf))
    elif kind == "equip":
        pools = {
            "weapon": (EQUIP_WEAPON_PREFIX, EQUIP_WEAPON_TYPES),
            "armor": (EQUIP_ARMOR_PREFIX, EQUIP_ARMOR_TYPES),
            "accessory": (EQUIP_ACC_PREFIX, EQUIP_ACC_TYPES),
        }
        pre, suf = pools.get(subtype, pools["weapon"])
        fill(lambda: random.choice(pre) + random.choice(suf))
    elif kind == "skill":
        pools = {
            "sword": (SKILL_SWORD_PREFIX, SKILL_SWORD_TYPES),
            "palm": (SKILL_PALM_PREFIX, SKILL_PALM_TYPES),
            "mind": (SKILL_MIND_PREFIX, SKILL_MIND_TYPES),
            "move": (SKILL_MOVE_PREFIX, SKILL_MOVE_TYPES),
        }
        pre, suf = pools.get(subtype, pools["sword"])
        fill(lambda: random.choice(pre) + random.choice(suf))
    elif kind == "monster":
        if subtype == "ling":
            fill(lambda: random.choice(MON_LING_MOD) + random.choice(MON_LING_CORE))
        elif subtype == "xiong":
            fill(lambda: random.choice(MON_XIONG_MOD) + random.choice(MON_XIONG_CORE))
        else:
            fill(lambda: random.choice(MON_YAO_MOD) + random.choice(MON_YAO_ANIMAL))
    else:  # other
        if subtype == "title":
            fill(lambda: random.choice(TITLE_REGION) + random.choice(TITLE_ROLE))
        elif subtype == "item":
            fill(lambda: random.choice(OTHER_ITEMS))
        else:
            tail = "丹" if random.random() < 0.8 else random.choice(PILL_TAIL)
            fill(lambda: random.choice(PILL_PREFIX) + tail)

    return list(results)
