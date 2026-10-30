"""
Fetch food items from Swiggy.

Swiggy has no official public API, so this file calls the same web endpoints
the Swiggy website uses. They can change or block requests at any time, so if
anything goes wrong we fall back to sample data and the app keeps working.
"""

import requests

HEADERS = {
    # Pretend to be a normal browser; Swiggy rejects requests without this.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

DISH_TYPE = "type.googleapis.com/swiggy.presentation.food.v2.Dish"
DISH_GROUP_TYPE = "type.googleapis.com/swiggy.presentation.food.v2.DishGroup"


def get_coordinates(location):
    """Turn a place name like 'Koramangala, Bangalore' into (lat, lng).

    Uses OpenStreetMap's free Nominatim service.
    """
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": location, "format": "json", "limit": 1},
        headers={"User-Agent": "swiggy-food-search-agent/1.0"},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise ValueError(f"Could not find location: {location}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def find_dish_cards(data):
    """Walk through Swiggy's deeply nested JSON and collect every dish card.

    Swiggy uses two shapes:
      - Dish:      one dish with its restaurant
      - DishGroup: one restaurant with a list of dishes
    Both are turned into {"info": ..., "restaurant": ...} cards.
    """
    dishes = []
    if isinstance(data, dict):
        if data.get("@type") == DISH_TYPE and "info" in data:
            dishes.append(data)
        elif data.get("@type") == DISH_GROUP_TYPE:
            for dish in data.get("dishes", []):
                dishes.append({"info": dish.get("info", {}), "restaurant": data.get("restaurant", {})})
        for value in data.values():
            dishes.extend(find_dish_cards(value))
    elif isinstance(data, list):
        for item in data:
            dishes.extend(find_dish_cards(item))
    return dishes


def to_number(value):
    """Convert values like '4.4' or 4.4 to a float; '--' or None become 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_dish(card):
    """Convert one raw Swiggy dish card into a simple dictionary."""
    info = card.get("info", {})
    restaurant = card.get("restaurant", {}).get("info", {})

    # Swiggy stores prices in paise (1 rupee = 100 paise).
    price = (info.get("price") or info.get("defaultPrice") or 0) / 100

    # Prefer the dish rating; fall back to the restaurant rating.
    dish_rating = to_number(info.get("ratings", {}).get("aggregatedRating", {}).get("rating"))
    rating = dish_rating or to_number(restaurant.get("avgRating"))

    return {
        "name": info.get("name", "Unknown"),
        "restaurant": restaurant.get("name", "Unknown"),
        "category": info.get("category", ""),
        "is_veg": bool(info.get("isVeg")),
        "price": price,
        "rating": rating,
        "delivery_time": restaurant.get("sla", {}).get("deliveryTime"),
        "description": info.get("description", ""),
    }


def search_swiggy(food_name, location):
    """Search live Swiggy data. Raises an error if anything fails."""
    lat, lng = get_coordinates(location)
    response = requests.get(
        "https://www.swiggy.com/dapi/restaurants/search/v3",
        params={
            "lat": lat,
            "lng": lng,
            "str": food_name,
            "submitAction": "ENTER",
            "selectedPLTab": "DISH",
        },
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    # Promoted dishes can appear twice, so skip duplicates.
    dishes = []
    seen = set()
    for card in find_dish_cards(response.json()):
        dish = parse_dish(card)
        key = (dish["name"], dish["restaurant"])
        if key not in seen:
            seen.add(key)
            dishes.append(dish)
    if not dishes:
        raise ValueError("Swiggy returned no dishes")
    return dishes


# ---------------------------------------------------------------------------
# Sample data used when the live Swiggy request fails
# ---------------------------------------------------------------------------

SAMPLE_FOOD = [
    {"name": "Chicken Dum Biryani", "restaurant": "Meghana Foods", "category": "Biryani", "is_veg": False, "price": 320, "rating": 4.6, "delivery_time": 35, "description": "Andhra style spicy biryani"},
    {"name": "Hyderabadi Chicken Biryani", "restaurant": "Paradise", "category": "Biryani", "is_veg": False, "price": 299, "rating": 4.4, "delivery_time": 30, "description": "Classic Hyderabadi dum biryani"},
    {"name": "Veg Biryani", "restaurant": "Behrouz Biryani", "category": "Biryani", "is_veg": True, "price": 249, "rating": 4.2, "delivery_time": 40, "description": "Fragrant rice with vegetables"},
    {"name": "Mutton Biryani", "restaurant": "Empire Restaurant", "category": "Biryani", "is_veg": False, "price": 380, "rating": 4.3, "delivery_time": 45, "description": "Slow cooked mutton biryani"},
    {"name": "Egg Biryani", "restaurant": "Empire Restaurant", "category": "Biryani", "is_veg": False, "price": 199, "rating": 4.1, "delivery_time": 45, "description": "Biryani with boiled eggs"},
    {"name": "Margherita Pizza", "restaurant": "Domino's Pizza", "category": "Pizza", "is_veg": True, "price": 239, "rating": 4.2, "delivery_time": 30, "description": "Classic cheese pizza"},
    {"name": "Farmhouse Pizza", "restaurant": "Domino's Pizza", "category": "Pizza", "is_veg": True, "price": 459, "rating": 4.4, "delivery_time": 30, "description": "Onion, capsicum, tomato, mushroom"},
    {"name": "Pepperoni Pizza", "restaurant": "Pizza Hut", "category": "Pizza", "is_veg": False, "price": 499, "rating": 4.0, "delivery_time": 35, "description": "Loaded with pepperoni"},
    {"name": "Neapolitan Margherita", "restaurant": "Pizza Bakery", "category": "Pizza", "is_veg": True, "price": 425, "rating": 4.7, "delivery_time": 40, "description": "Wood-fired sourdough pizza"},
    {"name": "Paneer Butter Masala", "restaurant": "Punjabi Tadka", "category": "North Indian", "is_veg": True, "price": 260, "rating": 4.3, "delivery_time": 35, "description": "Paneer in creamy tomato gravy"},
    {"name": "Butter Chicken", "restaurant": "Punjabi Tadka", "category": "North Indian", "is_veg": False, "price": 340, "rating": 4.5, "delivery_time": 35, "description": "Chicken in rich butter gravy"},
    {"name": "Masala Dosa", "restaurant": "Vidyarthi Bhavan", "category": "South Indian", "is_veg": True, "price": 90, "rating": 4.8, "delivery_time": 25, "description": "Crispy dosa with potato filling"},
    {"name": "Idli Vada", "restaurant": "MTR", "category": "South Indian", "is_veg": True, "price": 110, "rating": 4.6, "delivery_time": 25, "description": "Steamed idli with crispy vada"},
    {"name": "Classic Veg Burger", "restaurant": "Burger King", "category": "Burgers", "is_veg": True, "price": 129, "rating": 4.0, "delivery_time": 25, "description": "Veg patty with fresh veggies"},
    {"name": "Crispy Chicken Burger", "restaurant": "McDonald's", "category": "Burgers", "is_veg": False, "price": 189, "rating": 4.2, "delivery_time": 25, "description": "Fried chicken patty burger"},
    {"name": "Hakka Noodles", "restaurant": "Mainland China", "category": "Chinese", "is_veg": True, "price": 280, "rating": 4.3, "delivery_time": 40, "description": "Stir-fried noodles with veggies"},
    {"name": "Chilli Chicken", "restaurant": "Mainland China", "category": "Chinese", "is_veg": False, "price": 360, "rating": 4.4, "delivery_time": 40, "description": "Indo-Chinese spicy chicken"},
    {"name": "Chocolate Brownie", "restaurant": "Theobroma", "category": "Desserts", "is_veg": True, "price": 150, "rating": 4.6, "delivery_time": 30, "description": "Rich fudgy brownie"},
]


def search_sample_data(food_name):
    """Search the sample data by food name or category (case-insensitive)."""
    words = food_name.lower().split()
    results = []
    for item in SAMPLE_FOOD:
        text = f"{item['name']} {item['category']}".lower()
        if any(word in text for word in words):
            results.append(dict(item))
    return results


def search_food(food_name, location):
    """Try live Swiggy first; use sample data if that fails.

    Returns (list_of_dishes, source) where source is 'swiggy' or 'sample'.
    """
    try:
        return search_swiggy(food_name, location), "swiggy"
    except Exception as error:
        print(f"\n[!] Live Swiggy search failed ({error}). Using sample data.")
        return search_sample_data(food_name), "sample"
