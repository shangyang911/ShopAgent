"""Fictional customer profiles for local demos; addresses are illustrative."""

_ROWS = [
    ("陈嘉宁", "13800126031", "jianing.chen", "北京市", "北京市", "朝阳区", "望京街道 望舒雅苑3号楼2单元602室", "100102"),
    ("林雨桐", "15600186072", "yutong.lin", "上海市", "上海市", "浦东新区", "花木街道 锦悦花园8号楼1203室", "201204"),
    ("周子谦", "13700157026", "ziqian.zhou", "浙江省", "杭州市", "西湖区", "蒋村街道 云栖雅居5幢1单元801室", "310030"),
    ("赵明远", "18600193058", "mingyuan.zhao", "河北省", "石家庄市", "长安区", "建北街道 和悦家园6号楼2单元503室", "050000"),
    ("王思涵", "13500168043", "sihan.wang", "广东省", "广州市", "天河区", "天园街道 悦景花园2栋1602室", "510665"),
    ("刘浩然", "15800175069", "haoran.liu", "广东省", "深圳市", "南山区", "粤海街道 海悦公寓B座902室", "518057"),
    ("许知夏", "18900136025", "zhixia.xu", "四川省", "成都市", "武侯区", "簇桥街道 锦棠小区7栋1单元1101室", "610041"),
    ("杨景川", "13600182047", "jingchuan.yang", "湖北省", "武汉市", "洪山区", "关山街道 星河雅苑4栋2单元702室", "430074"),
    ("孙悦宁", "18700156083", "yuening.sun", "江苏省", "南京市", "建邺区", "沙洲街道 江悦府9幢1单元1002室", "210019"),
    ("吴承宇", "15000192036", "chengyu.wu", "山东省", "青岛市", "市南区", "金门路街道 海棠里2号楼3单元401室", "266071"),
    ("郑可欣", "18200167054", "kexin.zheng", "陕西省", "西安市", "雁塔区", "电子城街道 长安雅庭6号楼1801室", "710065"),
    ("何书言", "13900148062", "shuyan.he", "福建省", "厦门市", "思明区", "莲前街道 鹭鸣花园3号楼2单元803室", "361008"),
    ("唐若溪", "15500173028", "ruoxi.tang", "湖南省", "长沙市", "岳麓区", "观沙岭街道 麓晴家园5栋1402室", "410013"),
    ("梁锦程", "18800165091", "jincheng.liang", "重庆市", "重庆市", "渝北区", "龙山街道 云锦新苑8幢1单元1204室", "401147"),
]

DEMO_CUSTOMERS = [
    dict(name=name, phone=phone, email=f"{email}@example.com", country="中国",
         province=province, city=city, district=district, detail=detail,
         postal_code=postal_code, longitude=None, latitude=None)
    for name, phone, email, province, city, district, detail, postal_code in _ROWS
]


def address_values(profile):
    return {"recipient_name": profile["name"], **{
        field: profile[field] for field in (
            "phone", "country", "province", "city", "district", "detail",
            "postal_code", "longitude", "latitude",
        )
    }}
