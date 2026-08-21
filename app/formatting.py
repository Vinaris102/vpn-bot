def format_bytes(num_bytes: int | float) -> str:
    """Переводит байты в удобочитаемый вид: 1234567 -> '1.18 GB'."""
    if not num_bytes:
        return "0 B"
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < step:
            return f"{num_bytes:.2f} {unit}" if unit != "B" else f"{int(num_bytes)} {unit}"
        num_bytes /= step
    return f"{num_bytes:.2f} PB"
