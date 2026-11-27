describe('Art volunteer canvas landing', () => {
  it('shows a full-screen example canvas to signed-out visitors', () => {
    cy.visit('/themes/art/')
    cy.get('.art-home').should('be.visible')
    cy.contains('示例展画').should('be.visible')
    cy.contains('鸢尾花').should('not.exist')
    cy.contains('文森特').should('not.exist')
    cy.get('.art-progress__number').should('be.visible')
    cy.get('.art-stage__canvas').should('be.visible')
    cy.contains('a', '登录').should('have.attr', 'href').and('include', '/user/login')
    cy.get('.yg-page-hex').should('not.exist')
  })

  it('fits a tablet viewport in both orientations and remembers the two controls', () => {
    cy.viewport(1024, 768)
    cy.visit('/themes/art/')
    cy.contains('button', '呼吸').should('have.attr', 'aria-pressed', 'false').click()
    cy.contains('button', '看全幅').click()
    cy.reload()
    cy.contains('button', '呼吸').should('have.attr', 'aria-pressed', 'true')
    const checkFrame = (width: number, height: number) => {
      cy.get('.art-stage__canvas').should(($el) => {
        const el = $el[0]!
        const rect = el.getBoundingClientRect()
        expect([rect.x, rect.y, rect.width, rect.height]).to.deep.equal([0, 0, width, height])
        expect(getComputedStyle(el).objectFit).to.equal('contain')
      })
    }
    checkFrame(1024, 768)
    cy.contains('a', '登录').click()
    cy.get('.yg-phi').should(($el) => {
      expect(getComputedStyle($el[0]!, '::after').content).to.equal('none')
    })
    cy.go('back')
    cy.viewport(768, 1024)
    checkFrame(768, 1024)
    cy.contains('button', '铺满画面').click()
    cy.get('.art-stage__canvas').should('have.css', 'object-fit', 'cover')
  })
})
