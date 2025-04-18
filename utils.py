from bson.objectid import ObjectId
from pymongo.collection import Collection

def add_forwarding_task(source_id: str, destination_id: str, task_type: str, collection: Collection) -> str:
    task = {
        "source_id": source_id,
        "destination_id": destination_id,
        "type": task_type
    }
    result = collection.insert_one(task)
    return str(result.inserted_id)

def get_forwarding_tasks(collection: Collection) -> list:
    return list(collection.find())

def remove_forwarding_task(task_id: str, collection: Collection) -> bool:
    result = collection.delete_one({"_id": ObjectId(task_id)})
    return result.deleted_count > 0
