from app.services.pec_page import navigate_pec, select_pec_page


class FakePage:
    def __init__(self, url: str):
        self.url = url
        self.closed = False
        self.front = False
        self.navigation = None

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def bring_to_front(self):
        self.front = True

    def goto(self, destination, wait_until):
        self.url = destination
        self.navigation = (destination, wait_until)


class FakeContext:
    def __init__(self, pages):
        self.pages = pages
        self.created = None

    def new_page(self):
        self.created = FakePage("about:blank")
        self.pages.append(self.created)
        return self.created


def test_existing_pec_tab_is_selected_and_extra_blank_tab_is_closed():
    blank = FakePage("about:blank")
    pec = FakePage("https://esus.exemplo.gov.br/lista-atendimento")
    context = FakeContext([blank, pec])

    selected = select_pec_page(context, "https://esus.exemplo.gov.br")

    assert selected is pec
    assert pec.front
    assert blank.closed
    assert context.created is None


def test_only_blank_tab_is_reused_instead_of_opening_another_tab():
    blank = FakePage("about:blank")
    context = FakeContext([blank])

    selected = select_pec_page(context, "https://esus.exemplo.gov.br")

    assert selected is blank
    assert selected.front
    assert not selected.closed
    assert context.created is None


def test_navigation_keeps_automated_page_in_front():
    page = FakePage("about:blank")

    navigate_pec(page, "https://esus.exemplo.gov.br/cidadao")

    assert page.navigation == (
        "https://esus.exemplo.gov.br/cidadao",
        "domcontentloaded",
    )
    assert page.front
