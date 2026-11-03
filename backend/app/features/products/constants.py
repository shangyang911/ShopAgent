"""商品可售状态的统一定义，供下单校验和库存监控共用。"""

# “缺货”属于可售业务状态；能否实际下单还需单独检查可用库存。
SELLABLE_STATUSES = ("Active", "Low stock", "Out of stock")
