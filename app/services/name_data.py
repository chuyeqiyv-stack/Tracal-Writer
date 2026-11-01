# -*- coding: utf-8 -*-
"""起名助手的本地字库字典。

全部为内置静态数据，不依赖网络。按「人物 / 地点」两大类，
中 / 西 / 日三种风格组织，供 NameGeneratorDialog 随机组合生成名字。
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
]

# 地点后缀（西方 / 日式）
WEST_PLACE_SUFFIX = ["城", "港", "镇", "堡", "谷", "原", "湖", "滩"]
JP_PLACE_SUFFIX = ["町", "村", "坂", "原", "川", "滨", "台", "乡"]


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


def generate_names(kind: str, style: str, gender: str, surname: str, given: str, count: int = 21) -> list[str]:
    """根据条件随机生成 count 个不重复的名字。

    kind:   person 人物 / place 地点
    style:  cn 中文 / west 西方 / jp 日式
    gender: male 男 / female 女 / any 不限
    """
    surname = surname.strip()
    given = given.strip()
    results: list[str] = []
    attempts = 0

    while len(results) < count and attempts < 600:
        attempts += 1
        if kind == "person":
            if style == "cn":
                name = _cn_person(gender, surname, given)
                if not name:
                    continue
            elif style == "west":
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
                name = f"{first}·{last}"
            else:
                if gender == "female":
                    first = random.choice(JP_FEMALE_FIRST)
                elif gender == "male":
                    first = random.choice(JP_MALE_FIRST)
                else:
                    first = random.choice(JP_FEMALE_FIRST + JP_MALE_FIRST)
                last = surname if surname else random.choice(JP_SURNAMES)
                if given:
                    first = given
                name = f"{last}{first}"
        else:  # 地点
            if style == "cn":
                if given:
                    name = given + random.choice(CN_PLACES_SUFFIX)
                elif surname:
                    name = surname + random.choice(CN_PLACES_SUFFIX)
                elif random.random() < 0.5:
                    name = random.choice(CN_PLACES_FULL)
                else:
                    name = random.choice(CN_PLACES_PREFIX) + random.choice(CN_PLACES_SUFFIX)
            elif style == "west":
                base = random.choice(WEST_PLACES)
                if given:
                    name = given + random.choice(WEST_PLACE_SUFFIX)
                elif surname:
                    name = surname + "兰" if random.random() < 0.4 else surname + random.choice(WEST_PLACE_SUFFIX)
                else:
                    name = base
            else:
                if given:
                    name = given + random.choice(JP_PLACE_SUFFIX)
                elif surname:
                    name = surname + random.choice(JP_PLACE_SUFFIX)
                else:
                    name = random.choice(JP_PLACES)

        if name and name not in results:
            results.append(name)

    return results
