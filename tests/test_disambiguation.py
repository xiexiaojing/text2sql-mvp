from __future__ import annotations

from text2sql_runtime.disambiguation import (
    PERSON_COUNT_CLARIFICATION_MESSAGE,
    PROPERTY_COMPANY_CONTACT_CLARIFICATION_MESSAGE,
    person_count_clarification_reason,
    property_company_contact_clarification_reason,
    resolve_person_count_choice,
    resolve_property_contact_role_choice,
    rewrite_person_count_follow_up,
    rewrite_property_company_contact_follow_up,
)


def test_person_count_clarification_for_ambiguous_community_question():
    assert person_count_clarification_reason("社区有多少人？") is None


def test_person_count_clarification_for_generic_umbrella_question():
    reason = person_count_clarification_reason("一共有多少人")
    assert reason == PERSON_COUNT_CLARIFICATION_MESSAGE


def test_person_count_clarification_skips_specific_resident_question():
    assert person_count_clarification_reason("本社区居民有多少") is None


def test_person_count_clarification_skips_grid_member_question():
    assert person_count_clarification_reason("社区网格员一共多少人？") is None


def test_person_count_clarification_skips_non_person_count_question():
    assert person_count_clarification_reason("社区有多少栋楼") is None


def test_resolve_person_count_choice_includes_liangwei_yizhan_aliases():
    assert resolve_person_count_choice("两委一站") == "组织管理在册人员有多少"
    assert resolve_person_count_choice("组织管理") == "组织管理在册人员有多少"
    assert resolve_person_count_choice("在册人员") == "组织管理在册人员有多少"
    assert resolve_person_count_choice("人口") == "本社区居民有多少"


def test_resolve_person_count_choice():
    assert resolve_person_count_choice("居民") == "本社区居民有多少"
    assert resolve_person_count_choice("查党员") == "本社区党员有多少"
    assert resolve_person_count_choice("两委一站") == "组织管理在册人员有多少"
    assert resolve_person_count_choice("社区有多少人？") is None


def test_person_count_clarification_skips_org_management_question():
    assert person_count_clarification_reason("组织管理在册人员有多少") is None


def test_rewrite_community_person_count_affirmative_follow_up():
    from text2sql_runtime.disambiguation import format_community_person_count_default_answer

    history = [
        {"role": "user", "content": "社区有多少人？"},
        {"role": "assistant", "content": format_community_person_count_default_answer(12)},
    ]
    assert rewrite_person_count_follow_up("是", history) == "组织管理在册人员有多少"
    assert rewrite_person_count_follow_up("居民", history) == "本社区居民有多少"


def test_rewrite_community_person_count_male_follow_up():
    from text2sql_runtime.disambiguation import format_community_person_count_default_answer

    history = [
        {"role": "user", "content": "咱社区有多少人"},
        {"role": "assistant", "content": format_community_person_count_default_answer(55)},
    ]
    assert rewrite_person_count_follow_up("男的呢", history) == "男性居民有多少"


def test_rewrite_person_count_male_follow_up_after_resident_choice():
    from text2sql_runtime.disambiguation import format_community_person_count_default_answer

    history = [
        {"role": "user", "content": "社区有多少人？"},
        {"role": "assistant", "content": format_community_person_count_default_answer(12)},
        {"role": "user", "content": "居民"},
        {"role": "assistant", "content": "本社区共有 100 位居民。"},
    ]
    assert rewrite_person_count_follow_up("男的呢", history) == "男性居民有多少"


def test_property_company_contact_clarification_for_responsible_person_question():
    reason = property_company_contact_clarification_reason("物业公司负责人是谁？")
    assert reason == PROPERTY_COMPANY_CONTACT_CLARIFICATION_MESSAGE


def test_property_company_contact_clarification_skips_phone_question():
    assert property_company_contact_clarification_reason("物业公司负责人电话多少？") is None


def test_property_company_contact_clarification_skips_manager_question():
    assert property_company_contact_clarification_reason("物业公司经理电话是多少？") is None


def test_property_company_contact_clarification_skips_grid_manager_question():
    assert property_company_contact_clarification_reason("重兴园3号楼归谁管？") is None


def test_resolve_property_contact_role_choice():
    assert resolve_property_contact_role_choice("经理") == "物业公司经理电话是多少"
    assert resolve_property_contact_role_choice("客服") == "物业客服电话是多少"


def test_rewrite_property_company_contact_role_follow_up():
    history = [
        {"role": "user", "content": "物业公司负责人电话多少？"},
        {"role": "assistant", "content": PROPERTY_COMPANY_CONTACT_CLARIFICATION_MESSAGE},
    ]
    assert rewrite_property_company_contact_follow_up("经理", history) == "物业公司经理电话是多少"
