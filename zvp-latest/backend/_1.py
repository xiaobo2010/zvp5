import database

async def f():
    await database.connect_to_mongo()
    print(dir(database.db.zvms_new.logs))
    await database.db.zvms_new.logs.delete_many({
        "type": "rule_violation",
        "request.url": {
            "$regex": "env",
            "$options": "i"
        }
    })
    await database.close_mongo_connection()