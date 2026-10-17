"""A model page can ask for less than a day: an hour of runs after a tuning change."""


def seed(app, model_id="qwen-small"):
    app.extensions["pb"].store.upsert_model_seen({"model_id": model_id, "name": model_id, "origin": "library"})
    return model_id


def test_a_range_shorter_than_a_day_is_not_clamped_up(client, app):
    """The page offers 1h/6h/12h, so a quarter of a day has to mean six hours."""
    model_id = seed(app)
    out = client.get(f"/api/inventory/model/{model_id}?days=0.25").get_json()
    assert out.get("ok") is True, out
    assert out["days"] == 0.25


def test_the_model_page_defaults_to_a_week(client, app):
    model_id = seed(app)
    out = client.get(f"/api/inventory/model/{model_id}").get_json()
    assert out.get("ok") is True and out["days"] == 7, out


def test_a_range_below_an_hour_is_raised_to_one(client, app):
    model_id = seed(app)
    out = client.get(f"/api/inventory/model/{model_id}?days=0.001").get_json()
    assert out["days"] == 1 / 24.0, out
