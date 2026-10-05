from supervisor.setup import workflow_summary, workflow_summary_html


def test_summary_resolves_connected_parameters_and_model_switch():
    graph = {
        "model": {"class_type": "PrimitiveString", "inputs": {"value": "actual.safetensors"}},
        "route": {"class_type": "Reroute", "inputs": {"input": ["model", 0]}},
        "switch": {"class_type": "ComfySwitchNode", "inputs": {"switch": True, "on_true": ["route", 0], "on_false": "unused"}},
        "strength": {"class_type": "PrimitiveFloat", "inputs": {"value": 0.65}},
        "lora": {"class_type": "LoraLoader", "inputs": {"lora_name": ["switch", 0], "strength_model": ["strength", 0], "strength_clip": 1.0}},
    }
    rows = workflow_summary(graph)
    assert rows[0][2] == "actual.safetensors"
    assert '"strength_model": 0.65' in rows[0][3]
    rendered = workflow_summary_html(rows)
    assert "模型强度" in rendered
    assert "0.65" in rendered


def test_unresolved_model_is_visible_and_cycles_do_not_crash():
    graph = {"loop": {"class_type": "PrimitiveString", "inputs": {"value": ["loop", 0]}},
             "loader": {"class_type": "UNETLoader", "inputs": {"unet_name": ["loop", 0]}}}
    assert "未解析：节点 loop" in workflow_summary(graph)[0][2]


def test_all_rows_render_without_virtualization_and_escape_names():
    rows = [[str(i), "LoraLoader", '<script>alert(1)</script>' + "long_model" * 30,
             '{"strength_model": 0.5}'] for i in range(15)]
    rendered = workflow_summary_html(rows)
    assert rendered.count('<td data-label="节点">') == 15
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_power_lora_loader_keeps_model_and_parameters():
    rows = workflow_summary({"1": {"class_type": "Power Lora Loader (rgthree)", "inputs": {
        "lora_1": {"lora": "one.safetensors", "strength": 0.4, "on": True}}}})
    assert rows[0][2] == "one.safetensors"
    assert '"strength": 0.4' in rows[0][3]
