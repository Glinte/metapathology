import mqt.ddsim

print(f"mqt.ddsim: {mqt.ddsim.DD_SIM_VALUE}")

try:
    import mqt.core
except ModuleNotFoundError as exception:
    print(f"mqt.core failed: {exception}")
else:
    print(f"mqt.core: {mqt.core.CORE_VALUE}")
