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

  it('toggles between cover and full artwork framing', () => {
    cy.visit('/themes/art/')
    cy.contains('button', '看全幅').click()
    cy.get('.art-home').should('have.class', 'art-home--full')
    cy.contains('button', '铺满画面').click()
    cy.get('.art-home').should('not.have.class', 'art-home--full')
  })
})
