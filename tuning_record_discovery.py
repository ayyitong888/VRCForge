"""Page existing saved tuning records without authorizing or applying them."""


def page_tuning_records(reader, key, arguments):
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", 20)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer from 1 to 50")
    avatar = arguments.get("avatarPath")
    if avatar is not None and not isinstance(avatar, str):
        raise ValueError("avatarPath must be a string")
    payload = reader(avatar)
    if not payload.get("ok"):
        return payload
    rows = payload[key]
    end = offset + limit
    return {
        **payload, key: rows[offset:end], "count": len(rows),
        "offset": offset, "limit": limit, "hasMore": end < len(rows),
        "nextOffset": end if end < len(rows) else None,
        "scope": "App saved records; avatar filter is not a project identity lock. Reapply revalidates the current target.",
    }
