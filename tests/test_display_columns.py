from __future__ import annotations

from text2sql_runtime.display_columns import filter_public_table, is_hidden_id_column
from text2sql_runtime.formatter import ResultFormatter


def test_is_hidden_id_column():
    assert is_hidden_id_column("id")
    assert is_hidden_id_column("merchant_id")
    assert not is_hidden_id_column("merchant_name")
    assert not is_hidden_id_column("channel_value")
    assert not is_hidden_id_column("total")


def test_filter_public_table_removes_id_columns():
    table = filter_public_table(
        {
            "columns": ["merchant_id", "merchant_name", "total"],
            "column_labels": ["商户ID", "商户名称", "数量"],
            "rows": [
                {
                    "merchant_id": "m-1",
                    "merchant_name": "Demo Store",
                    "total": 12,
                }
            ],
            "row_count": 1,
            "mode": "live",
        }
    )
    assert table is not None
    assert table["columns"] == ["merchant_name", "total"]
    assert table["column_labels"] == ["商户名称", "数量"]
    assert table["rows"] == [{"merchant_name": "Demo Store", "total": 12}]


def test_formatter_table_omits_id_columns():
    formatter = ResultFormatter()
    rows = [
        {
            "id": "order-1",
            "merchant_name": "Demo Store",
            "total": 100,
        }
    ]
    _answer, table = formatter.format(
        type(
            "Execution",
            (),
            {
                "mode": "live",
                "columns": list(rows[0].keys()),
                "rows": rows,
            },
        )(),
        "SELECT po.id, m.name AS merchant_name, SUM(po.amount) AS total FROM payment_order po",
    )
    assert table is not None
    assert table["columns"] == ["merchant_name", "total"]
    assert table["rows"] == [{"merchant_name": "Demo Store", "total": 100}]
