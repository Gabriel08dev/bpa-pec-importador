from __future__ import annotations

from app.models import ExistingProduction, Patient, ReviewItem, Status
from app.services.sheet_allocator import allocate


def item(row, professional="P1", day="20260701", cpf=None):
    return ReviewItem(row=row, original_identifier="", professional_name=professional, professional_cns=professional,
                      cbo="225250", cnes="4998782", procedure="0301010072", service_date=day,
                      informed_birth="20000101", original_cid="", patient=Patient(cpf=cpf or f"{row:011d}", cns=f"7{row:014d}"),
                      status=Status.PEC_CONFIRMADO, sources={"competence":"202607"})


def existing(day="20260701", sheet="001", seq="01", professional="P1", cpf="99999999999", cid="", cnes="4998782"):
    return ExistingProduction(professional,cnes,"225250","202607",day,sheet,seq,cpf,"",cid)


def test_day_01_is_sheet_001(): assert allocate([item(1)], [])[0].sheet == "001"
def test_day_03_is_sheet_002():
    rows = allocate([item(1,day="20260701"),item(2,day="20260703")], [])
    assert [row.sheet for row in rows] == ["001","002"]
def test_dates_out_of_order_are_sorted():
    rows = [item(2,day="20260703"),item(1,day="20260701")]; allocate(rows, [])
    assert next(x for x in rows if x.service_date.endswith("01")).sheet == "001"
def test_other_professional_starts_sheet_001():
    rows=allocate([item(1,"P1"),item(2,"P2")],[]); assert [x.sheet for x in rows]==["001","001"]
def test_twenty_on_one_sheet(): assert {x.sheet for x in allocate([item(i) for i in range(1,21)],[])} == {"001"}
def test_twenty_one_create_second_sheet(): assert allocate([item(i) for i in range(1,22)],[])[-1].sheet == "002"
def test_next_date_after_overflow_uses_third_sheet():
    rows=[item(i) for i in range(1,22)]+[item(30,day="20260703")]; allocate(rows,[]); assert rows[-1].sheet=="003"
def test_existing_sheet_preserved_and_free_sequence_filled():
    row=allocate([item(1)], [existing(seq="01")])[0]; assert (row.sheet,row.sequence)==("001","02")
def test_existing_full_sheet_creates_next():
    old=[existing(seq=f"{i:02d}",cpf=f"8{i:010d}") for i in range(1,21)]
    assert allocate([item(1)],old)[0].sheet=="002"
def test_new_date_after_existing_uses_next_free_sheet():
    assert allocate([item(1,day="20260703")],[existing(day="20260701",sheet="004")])[0].sheet=="005"
def test_does_not_mix_dates():
    rows=allocate([item(1,day="20260701"),item(2,day="20260702")],[]); assert rows[0].sheet!=rows[1].sheet
def test_does_not_mix_professionals():
    rows=allocate([item(1,"P1"),item(2,"P2")],[]); assert all(x.sheet=="001" for x in rows)
def test_same_patient_two_professionals_allowed():
    rows=allocate([item(1,"P1",cpf="123"),item(2,"P2",cpf="123")],[]); assert all(x.status==Status.PRONTO_PARA_ADICIONAR for x in rows)
def test_same_patient_two_dates_allowed():
    rows=allocate([item(1,day="20260701",cpf="123"),item(2,day="20260702",cpf="123")],[]); assert all(x.status==Status.PRONTO_PARA_ADICIONAR for x in rows)
def test_duplicate_same_patient_professional_date_ignored():
    rows=allocate([item(1,cpf="123"),item(2,cpf="123")],[]); assert rows[1].status==Status.IGNORADO_DUPLICIDADE
def test_existing_duplicate_is_not_added(): assert allocate([item(1,cpf="99999999999")],[existing()])[0].status==Status.JA_EXISTE_NO_BPA


def test_existing_exact_visit_without_cid_is_marked_for_cid_update():
    row = item(1, cpf="99999999999")
    row.final_cid = "Z713"
    allocate([row], [existing(sheet="010", seq="07", cid="")])
    assert row.status == Status.PRONTO_PARA_ATUALIZAR_CID
    assert (row.sheet, row.sequence) == ("010", "07")
    assert "somente o CID" in row.reason


def test_existing_cid_is_never_overwritten():
    row = item(1, cpf="99999999999")
    row.final_cid = "Z713"
    allocate([row], [existing(cid="E119")])
    assert row.status == Status.JA_EXISTE_NO_BPA
    assert "valor existente será preservado" in row.reason


def test_only_one_update_is_planned_for_duplicate_sheet_rows():
    first = item(1, cpf="99999999999")
    second = item(2, cpf="99999999999")
    first.final_cid = second.final_cid = "Z713"
    allocate([first, second], [existing(cid="")])
    assert first.status == Status.PRONTO_PARA_ATUALIZAR_CID
    assert second.status == Status.IGNORADO_DUPLICIDADE


def test_existing_same_patient_and_professional_on_another_day_is_added_on_new_sheet():
    row=allocate([item(1,day="20260702",cpf="99999999999")],[existing(day="20260701",sheet="004")])[0]
    assert row.status==Status.PRONTO_PARA_ADICIONAR
    assert row.sheet=="005" and row.sequence=="01"


def test_existing_same_cns_professional_and_date_is_skipped_even_if_cpf_differs():
    row=item(1,cpf="11111111111")
    old=existing(cpf="22222222222")
    old.patient_cns=row.patient.cns
    assert allocate([row],[old])[0].status==Status.JA_EXISTE_NO_BPA


def test_existing_wrong_cnes_is_moved_to_ceae_with_new_destination_slot():
    row = item(1, cpf="99999999999")
    old = existing(sheet="004", seq="03", cnes="2114771", cid="E119")
    destination = existing(sheet="007", seq="01", cpf="11111111111", cnes="4998782")
    allocate([row], [old, destination])
    assert row.status == Status.JA_EXISTE_NO_BPA
    assert row.cnes == "4998782"
    assert (row.sheet, row.sequence) == ("", "")
    assert "nenhum campo" in row.reason


def test_existing_wrong_cnes_and_empty_cid_plans_one_combined_update():
    row = item(1, cpf="99999999999")
    row.final_cid = "Z713"
    allocate([row], [existing(cnes="2114771", cid="")])
    assert row.status == Status.PRONTO_PARA_CORRIGIR_CNES_E_CID
    assert "2114771" in row.reason and "4998782" in row.reason


def test_existing_correct_ceae_and_empty_cid_remains_cid_only_update():
    row = item(1, cpf="99999999999")
    row.final_cid = "Z713"
    allocate([row], [existing(cnes="4998782", cid="")])
    assert row.status == Status.PRONTO_PARA_ATUALIZAR_CID


def test_wrong_cnes_with_filled_cid_preserves_existing_cid():
    row = item(1, cpf="99999999999")
    row.final_cid = "Z713"
    allocate([row], [existing(cnes="2114771", cid="E119")])
    assert row.status == Status.JA_EXISTE_NO_BPA
    assert "nenhum campo" in row.reason
