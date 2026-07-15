import bt_repro


def use_template() -> str:
    return bt_repro.TEMPLATE.format(which="Bar")
