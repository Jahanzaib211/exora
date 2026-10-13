from prospector.crawl.extract import candidate_links, extract_page

TEAM_PAGE = """
<html><head><title>Our people | Smith Law</title>
<script type="application/ld+json">{"@type": "Person", "name": "Grace Hopper", "jobTitle": "Managing Partner",
 "email": "mailto:grace.hopper@smithlaw.co.uk"}</script></head>
<body>
<h2>Our Senior Management Team</h2>
<div class="team-member"><h3>Jane Doe</h3><p>Partner &amp; Head of Employment</p>
  <a href="mailto:jane.doe@smithlaw.co.uk">Email Jane</a></div>
<div class="team-member"><h3>Amber Keye</h3>
  <div class="team-member"><h3>Amy Osman</h3><p>Senior Associate Solicitor</p></div></div>
<div class="team-member"><h3>Chief Executive</h3><p>Dean Orgill</p></div>
<h3>Family Law</h3><p>Solicitors for families</p>
<p>Contact: info@smithlaw.co.uk, someone@gmail.com, logo@2x.png, partner@otherfirm.com</p>
<a href="tel:+44 161 832 1234">Call</a><a href="tel:12">bad</a>
<a href="https://www.linkedin.com/company/smith-law/?trk=x">LinkedIn</a>
</body></html>
"""


def test_extracts_only_published_business_contacts():
    f = extract_page(TEAM_PAGE, "https://www.smithlaw.co.uk/our-people")
    assert f.emails == {"jane.doe@smithlaw.co.uk", "info@smithlaw.co.uk", "grace.hopper@smithlaw.co.uk"}
    assert f.phones == {"+441618321234"}
    assert f.linkedin == {"https://www.linkedin.com/company/smith-law"}


def test_people_precision():
    f = extract_page(TEAM_PAGE, "https://www.smithlaw.co.uk/our-people")
    people = {p.name: p for p in f.people}
    assert people["Grace Hopper"].title == "Managing Partner"  # JSON-LD
    assert people["Jane Doe"].title == "Partner & Head of Employment"
    assert people["Jane Doe"].email == "jane.doe@smithlaw.co.uk"
    assert people["Amy Osman"].title == "Senior Associate Solicitor"
    # headings that are section titles or job titles are never people
    for not_person in ("Our Senior Management Team", "Chief Executive", "Family Law"):
        assert not_person not in people
    # a card without its own title must not borrow the next person's
    assert "Amber Keye" not in people or people["Amber Keye"].title in (None, "")


def test_candidate_links_same_site_and_team_first():
    html = """<a href="/contact-us">c</a><a href="/our-team/">t</a><a href="https://other.com/team">x</a>
              <a href="/blog/post">b</a><a href="mailto:a@b.com">m</a>"""
    links = candidate_links(html, "https://www.smithlaw.co.uk/")
    assert links == ["https://www.smithlaw.co.uk/our-team/", "https://www.smithlaw.co.uk/contact-us"]


PERSON_CONTACTS = """
<html><head><title>Team | Acme</title>
<script type="application/ld+json">{"@type": "Person", "name": "Sara Nolan", "jobTitle": "Director",
 "telephone": "+44 161 832 9999",
 "sameAs": ["https://www.linkedin.com/in/sara-nolan/?trk=x", "https://twitter.com/saranolan"]}</script>
</head><body>
<div class="team-member"><h3>Tom Reed</h3><p>Founder and CEO</p>
  <a href="tel:+441618329000">Direct</a>
  <a href="https://uk.linkedin.com/in/tom-reed?trk=profile">in</a>
  <a href="mailto:tom.reed@acme.example">Email</a></div>
<div class="team-member"><h3>Priya Raman</h3><p>Head of Sales</p>
  <a href="https://www.linkedin.com/shareArticle?url=x">share</a>
  <a href="https://www.linkedin.com/company/acme">company</a></div>
</body></html>
"""


def test_person_direct_dial_and_profile_come_from_their_own_card():
    f = extract_page(PERSON_CONTACTS, "https://acme.example/team")
    people = {p.name: p for p in f.people}
    assert people["Tom Reed"].phone == "+441618329000"
    assert people["Tom Reed"].linkedin_url == "https://uk.linkedin.com/in/tom-reed"
    # the JSON-LD Person node carries the same two fields
    assert people["Sara Nolan"].phone == "+441618329999"
    assert people["Sara Nolan"].linkedin_url == "https://www.linkedin.com/in/sara-nolan"


def test_share_and_company_links_never_become_a_persons_profile():
    f = extract_page(PERSON_CONTACTS, "https://acme.example/team")
    people = {p.name: p for p in f.people}
    # a share link identifies no one, and a company link stays a company link
    assert people["Priya Raman"].linkedin_url is None
    assert f.linkedin == {"https://www.linkedin.com/company/acme"}
